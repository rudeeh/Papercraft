"""
Evaluation Runner (Phase 18.2)

Runs the questions in evaluation/questions.json through graph-only,
vector-only, and hybrid retrieval (via HybridRetriever's force_mode),
scores each mode against the question's expected_entities /
expected_relation / expected_sources, and writes a JSON report comparing
the three retrieval modes.

Usage:
    python -m evaluation.run_eval [--questions PATH] [--output PATH]
                                   [--top-k N] [--limit N]

Requires a running Neo4j + Qdrant with at least some papers already
ingested (the same services app.worker.tasks and app.api.graph_routes
use) -- this evaluates retrieval quality against the ontology, not the
correctness of the ingested content itself.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MODES = ("graph", "vector", "hybrid")


# ======================================================================
# Pure scoring functions -- no I/O, unit-testable without live infra.
# ======================================================================

def entity_names_from_facts(facts: List[Dict[str, Any]]) -> List[str]:
    """Every subject/object entity name mentioned across a list of graph facts."""
    names: List[str] = []
    for fact in facts:
        for node in (fact.get("subject"), fact.get("object")):
            if node and node.get("name"):
                names.append(node["name"])
    return names


def score_retrieval_hit(question: Dict[str, Any], found_entity_names: List[str]) -> bool:
    """True if every entity the question expects was actually retrieved."""
    expected = question.get("expected_entities") or []
    if not expected:
        return True  # nothing to check -- doesn't count against the hit rate
    found_lower = {n.lower() for n in found_entity_names}
    return all(e.lower() in found_lower for e in expected)


def score_source_correctness(
    question: Dict[str, Any], source_paper_ids: List[str]
) -> Optional[bool]:
    """True/False if the question names expected sources, else None (not checkable)."""
    expected = question.get("expected_sources") or []
    if not expected:
        return None
    found = set(source_paper_ids)
    return any(pid in found for pid in expected)


def score_citation_correctness(
    question: Dict[str, Any], graph_facts: List[Dict[str, Any]]
) -> Optional[bool]:
    """True/False if the question names an expected relation, else None."""
    expected_relation = question.get("expected_relation")
    if not expected_relation:
        return None
    return any(fact.get("relation") == expected_relation for fact in graph_facts)


def score_answer_grounding(answer: str, sources: List[Dict[str, Any]], no_context_answer: str) -> bool:
    """
    An answer counts as "grounded" if the generator didn't have to fall back
    to its no-context refusal and at least one source paper backs it.
    """
    return answer != no_context_answer and len(sources) > 0


def evaluate_question(
    question: Dict[str, Any],
    hybrid_retriever,
    answer_generator,
    top_k: int,
) -> Dict[str, Any]:
    """Run one question through all three retrieval modes and score each."""
    from app.llm.answer_generator import NO_CONTEXT_ANSWER

    result: Dict[str, Any] = {
        "id": question.get("id"),
        "question": question["question"],
        "category": question.get("category"),
        "modes": {},
    }

    for mode in MODES:
        force_mode = None if mode == "hybrid" else mode
        retrieval = hybrid_retriever.retrieve(question["question"], top_k=top_k, force_mode=force_mode)
        generated = answer_generator.generate(question["question"], retrieval)

        found_entities = entity_names_from_facts(retrieval["graph_facts"])
        found_entities += [
            r["node_name"] for r in retrieval["vector_results"] if r.get("node_name")
        ]

        result["modes"][mode] = {
            "retrieval_hit": score_retrieval_hit(question, found_entities),
            "source_correct": score_source_correctness(question, retrieval["source_paper_ids"]),
            "citation_correct": score_citation_correctness(question, retrieval["graph_facts"]),
            "answer_grounded": score_answer_grounding(
                generated["answer"], generated["sources"], NO_CONTEXT_ANSWER,
            ),
            "source_paper_ids": retrieval["source_paper_ids"],
            "graph_facts_count": len(retrieval["graph_facts"]),
            "vector_results_count": len(retrieval["vector_results"]),
        }

    return result


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-mode rates across every evaluated question."""
    summary: Dict[str, Any] = {}
    for mode in MODES:
        mode_results = [r["modes"][mode] for r in results]
        hits = [m["retrieval_hit"] for m in mode_results]
        grounded = [m["answer_grounded"] for m in mode_results]
        source_checks = [m["source_correct"] for m in mode_results if m["source_correct"] is not None]
        citation_checks = [m["citation_correct"] for m in mode_results if m["citation_correct"] is not None]

        summary[mode] = {
            "retrieval_hit_rate": _rate(hits),
            "answer_grounded_rate": _rate(grounded),
            "source_correctness_rate": _rate(source_checks) if source_checks else None,
            "citation_correctness_rate": _rate(citation_checks) if citation_checks else None,
            "questions_evaluated": len(mode_results),
        }
    return summary


def _rate(values: List[bool]) -> float:
    return round(sum(1 for v in values if v) / len(values), 4) if values else 0.0


# ======================================================================
# CLI entry point -- wires up real Neo4j/Qdrant/LLM services.
# ======================================================================

def load_questions(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate GraphRAG retrieval quality.")
    parser.add_argument(
        "--questions", type=Path, default=Path(__file__).parent / "questions.json",
        help="Path to the evaluation questions JSON file.",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).parent / "results.json",
        help="Path to write the JSON report to.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Vector results per question.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Evaluate only the first N questions (for a quick smoke test).",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    # Imports are lazy so `--help` works without a configured environment,
    # and so unit tests can import the scoring functions above without
    # needing Neo4j/Qdrant/LLM settings at all.
    from app.core.config import settings
    from app.storage.neo4j_client import Neo4jClient
    from app.storage.qdrant_client import QdrantClientWrapper
    from app.storage.vector_repository import VectorRepository
    from app.embeddings.embedder import EmbeddingService
    from app.retrieval.graph_retriever import GraphRetriever
    from app.retrieval.vector_retriever import VectorRetriever
    from app.retrieval.citation_expander import CitationExpander
    from app.retrieval.hybrid_retriever import HybridRetriever
    from app.llm.answer_generator import AnswerGenerator

    questions = load_questions(args.questions)
    if args.limit:
        questions = questions[: args.limit]

    neo4j_client = Neo4jClient(
        uri=settings.NEO4J_URI, user=settings.NEO4J_USER,
        password=settings.NEO4J_PASSWORD, database=settings.NEO4J_DATABASE,
    )
    neo4j_client.connect()

    qdrant = QdrantClientWrapper(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    qdrant.connect()
    embedder = EmbeddingService(
        provider=settings.EMBEDDING_PROVIDER, model_name=settings.EMBEDDING_MODEL,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
    )
    vector_repo = VectorRepository(qdrant, embedder, collection_name=settings.QDRANT_COLLECTION_NAME)

    hybrid_retriever = HybridRetriever(
        graph_retriever=GraphRetriever(neo4j_client),
        vector_retriever=VectorRetriever(vector_repo),
        citation_expander=CitationExpander(neo4j_client),
    )
    answer_generator = AnswerGenerator()

    results = []
    t0 = time.time()
    try:
        for i, question in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] {question['question']}")
            results.append(evaluate_question(question, hybrid_retriever, answer_generator, args.top_k))
    finally:
        neo4j_client.close()

    report = {
        "questions_evaluated": len(results),
        "top_k": args.top_k,
        "duration_seconds": round(time.time() - t0, 2),
        "summary": summarize(results),
        "results": results,
    }

    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {len(results)} results to {args.output}")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
