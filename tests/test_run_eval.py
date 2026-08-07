"""Tests for the Phase 18 evaluation runner (evaluation/run_eval.py)."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evaluation.run_eval import (
    entity_names_from_facts,
    score_retrieval_hit,
    score_source_correctness,
    score_citation_correctness,
    score_answer_grounding,
    evaluate_question,
    summarize,
    load_questions,
    MODES,
)

QUESTIONS_PATH = Path(__file__).parent.parent / "evaluation" / "questions.json"

FACT = {
    "subject": {"name": "GPT", "type": "Method", "paper_id": "p2"},
    "relation": "IMPROVES_UPON",
    "object": {"name": "RNN", "type": "Method", "paper_id": None},
    "evidence": "GPT outperforms RNN.",
    "source_paper_ids": ["p2"],
}


class TestEntityNamesFromFacts:
    def test_collects_subject_and_object_names(self):
        names = entity_names_from_facts([FACT])
        assert names == ["GPT", "RNN"]

    def test_empty_list(self):
        assert entity_names_from_facts([]) == []


class TestScoreRetrievalHit:
    def test_true_when_no_expected_entities(self):
        assert score_retrieval_hit({"expected_entities": []}, []) is True

    def test_true_when_all_expected_found_case_insensitive(self):
        q = {"expected_entities": ["gpt", "RNN"]}
        assert score_retrieval_hit(q, ["GPT", "rnn"]) is True

    def test_false_when_missing_expected_entity(self):
        q = {"expected_entities": ["GPT", "BERT"]}
        assert score_retrieval_hit(q, ["GPT"]) is False


class TestScoreSourceCorrectness:
    def test_none_when_no_expected_sources(self):
        assert score_source_correctness({"expected_sources": []}, ["p1"]) is None

    def test_true_when_any_expected_source_found(self):
        q = {"expected_sources": ["p1", "p2"]}
        assert score_source_correctness(q, ["p2", "p3"]) is True

    def test_false_when_no_expected_source_found(self):
        q = {"expected_sources": ["p1"]}
        assert score_source_correctness(q, ["p3"]) is False


class TestScoreCitationCorrectness:
    def test_none_when_no_expected_relation(self):
        assert score_citation_correctness({"expected_relation": None}, [FACT]) is None

    def test_true_when_relation_present(self):
        q = {"expected_relation": "IMPROVES_UPON"}
        assert score_citation_correctness(q, [FACT]) is True

    def test_false_when_relation_absent(self):
        q = {"expected_relation": "CITES"}
        assert score_citation_correctness(q, [FACT]) is False


class TestScoreAnswerGrounding:
    def test_false_when_no_context_answer(self):
        assert score_answer_grounding("NO_CONTEXT", [{"paper_id": "p1"}], "NO_CONTEXT") is False

    def test_false_when_no_sources(self):
        assert score_answer_grounding("A real answer.", [], "NO_CONTEXT") is False

    def test_true_when_answer_and_sources_present(self):
        assert score_answer_grounding("A real answer.", [{"paper_id": "p1"}], "NO_CONTEXT") is True


class TestEvaluateQuestion:
    def _make_hybrid(self):
        hybrid = MagicMock()
        hybrid.retrieve.return_value = {
            "graph_facts": [FACT],
            "vector_results": [],
            "citation_paths": [],
            "source_paper_ids": ["p2"],
        }
        return hybrid

    def _make_generator(self):
        generator = MagicMock()
        generator.generate.return_value = {
            "answer": "GPT improves on RNN.",
            "sources": [{"paper_id": "p2", "title": None}],
            "graph_facts_used": [FACT],
            "confidence_notes": [],
        }
        return generator

    def test_runs_all_three_modes(self):
        hybrid = self._make_hybrid()
        generator = self._make_generator()
        question = {
            "id": "q1", "question": "How does GPT compare to RNN?", "category": "COMPARISON",
            "expected_entities": ["GPT"], "expected_relation": "IMPROVES_UPON",
            "expected_sources": ["p2"],
        }

        result = evaluate_question(question, hybrid, generator, top_k=5)

        assert set(result["modes"].keys()) == set(MODES)
        assert hybrid.retrieve.call_count == 3
        force_modes_used = {c.kwargs["force_mode"] for c in hybrid.retrieve.call_args_list}
        assert force_modes_used == {"graph", "vector", None}

    def test_scores_are_computed_per_mode(self):
        hybrid = self._make_hybrid()
        generator = self._make_generator()
        question = {
            "id": "q1", "question": "q", "category": "COMPARISON",
            "expected_entities": ["GPT"], "expected_relation": "IMPROVES_UPON",
            "expected_sources": ["p2"],
        }

        result = evaluate_question(question, hybrid, generator, top_k=5)

        for mode in MODES:
            assert result["modes"][mode]["retrieval_hit"] is True
            assert result["modes"][mode]["citation_correct"] is True
            assert result["modes"][mode]["source_correct"] is True
            assert result["modes"][mode]["answer_grounded"] is True


class TestSummarize:
    def test_aggregates_rates_across_questions(self):
        results = [
            {"modes": {
                "graph": {"retrieval_hit": True, "answer_grounded": True, "source_correct": True, "citation_correct": True},
                "vector": {"retrieval_hit": False, "answer_grounded": False, "source_correct": None, "citation_correct": None},
                "hybrid": {"retrieval_hit": True, "answer_grounded": True, "source_correct": True, "citation_correct": True},
            }},
            {"modes": {
                "graph": {"retrieval_hit": True, "answer_grounded": False, "source_correct": False, "citation_correct": True},
                "vector": {"retrieval_hit": True, "answer_grounded": True, "source_correct": None, "citation_correct": None},
                "hybrid": {"retrieval_hit": True, "answer_grounded": True, "source_correct": True, "citation_correct": True},
            }},
        ]

        summary = summarize(results)

        assert summary["graph"]["retrieval_hit_rate"] == 1.0
        assert summary["graph"]["answer_grounded_rate"] == 0.5
        assert summary["graph"]["source_correctness_rate"] == 0.5
        assert summary["vector"]["source_correctness_rate"] is None
        assert summary["hybrid"]["retrieval_hit_rate"] == 1.0
        assert summary["hybrid"]["questions_evaluated"] == 2

    def test_empty_results(self):
        summary = summarize([])
        for mode in MODES:
            assert summary[mode]["retrieval_hit_rate"] == 0.0
            assert summary[mode]["questions_evaluated"] == 0


class TestQuestionsFile:
    def test_loads_and_has_at_least_fifty_questions(self):
        questions = load_questions(QUESTIONS_PATH)
        assert len(questions) >= 50

    def test_covers_all_six_categories(self):
        questions = load_questions(QUESTIONS_PATH)
        categories = {q["category"] for q in questions}
        assert categories == {
            "EXPLANATION", "COMPARISON", "EVOLUTION",
            "CITATION", "SURVEY", "ENTITY_LOOKUP",
        }

    def test_every_question_has_required_fields(self):
        questions = load_questions(QUESTIONS_PATH)
        for q in questions:
            assert q["id"]
            assert q["question"]
            assert "expected_entities" in q
            assert "expected_relation" in q
            assert "expected_sources" in q

    def test_ids_are_unique(self):
        questions = load_questions(QUESTIONS_PATH)
        ids = [q["id"] for q in questions]
        assert len(ids) == len(set(ids))


class TestCliHelp:
    def test_help_works_without_configured_environment(self):
        """--help must not require Neo4j/Qdrant/LLM settings (lazy imports)."""
        result = subprocess.run(
            [sys.executable, "-m", "evaluation.run_eval", "--help"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "Evaluate GraphRAG retrieval quality" in result.stdout
