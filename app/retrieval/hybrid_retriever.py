"""
Hybrid Retriever Module (Phase 13.2)

Classifies a query (Phase 13.1) and routes it to graph retrieval (Phase 11),
vector retrieval (Phase 12), citation expansion (Phase 14), or a combination,
per the routing table in AGENT_PHASES.md Phase 13:

    EXPLANATION   -> Vector Retrieval
    CITATION      -> Graph Retrieval
    EVOLUTION     -> Citation Expansion + Graph Retrieval
    COMPARISON    -> Graph Retrieval + Vector Retrieval
    SURVEY        -> Graph Retrieval + Vector Retrieval
    ENTITY_LOOKUP -> Graph Retrieval

Graph retrieval is anchored two ways: (1) entities mentioned in the query
text itself, spotted with the same deterministic term-matching the Phase 5
EntityExtractor uses on paper text, and (2) an optional explicit
``paper_id`` when the caller already knows which paper the question is
about. Results are merged into a single structured payload ready for the
Phase 15 context builder.
"""

from typing import Any, Dict, List, Optional

import structlog

from app.retrieval.query_classifier import QueryClassifier, QueryType
from app.retrieval.graph_retriever import GraphRetriever
from app.retrieval.vector_retriever import VectorRetriever
from app.retrieval.citation_expander import CitationExpander
from app.graph.entity_extractor import EntityExtractor

logger = structlog.get_logger()

_WANTS_VECTOR = {QueryType.EXPLANATION, QueryType.COMPARISON, QueryType.SURVEY}
_WANTS_GRAPH = {
    QueryType.CITATION, QueryType.EVOLUTION, QueryType.COMPARISON,
    QueryType.SURVEY, QueryType.ENTITY_LOOKUP,
}


class HybridRetriever:
    """Classifies a query and routes it to graph and/or vector retrieval."""

    def __init__(
        self,
        graph_retriever: Optional[GraphRetriever] = None,
        vector_retriever: Optional[VectorRetriever] = None,
        citation_expander: Optional[CitationExpander] = None,
        classifier: Optional[QueryClassifier] = None,
        entity_extractor: Optional[EntityExtractor] = None,
    ) -> None:
        self._graph = graph_retriever
        self._vector = vector_retriever
        self._citations = citation_expander
        self._classifier = classifier or QueryClassifier()
        self._entity_extractor = entity_extractor or EntityExtractor()

    def retrieve(
        self,
        query: str,
        paper_id: Optional[str] = None,
        top_k: int = 5,
        force_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Classify *query* and run the retrieval combination its type calls for.

        Parameters
        ----------
        query : the natural-language question
        paper_id : optional -- anchors CITATION/EVOLUTION graph traversal
            and citation expansion to a specific paper, when the caller
            already knows which one the question concerns
        top_k : number of vector results to request
        force_mode : ``None`` (default -- use the classifier's routing
            table) | ``"graph"`` | ``"vector"`` | ``"both"``. Bypasses the
            routing table and always uses the given combination,
            regardless of query type. Used by the Phase 18 evaluation
            runner to compare retrieval modes independent of how a given
            question happens to get classified.

        Returns
        -------
        {
          "query": str,
          "query_type": str,
          "graph_facts": [...],        # subject/relation/object facts
          "vector_results": [...],     # normalized VectorRetriever hits
          "citation_paths": [...],     # CitationExpander results (EVOLUTION only)
          "source_paper_ids": [...],   # union across every result
        }
        """
        query_type = self._classifier.classify(query)

        if force_mode == "graph":
            use_graph, use_vector = True, False
        elif force_mode == "vector":
            use_graph, use_vector = False, True
        elif force_mode == "both":
            use_graph, use_vector = True, True
        else:
            use_graph = query_type in _WANTS_GRAPH
            use_vector = query_type in _WANTS_VECTOR

        graph_facts: List[Dict[str, Any]] = []
        vector_results: List[Dict[str, Any]] = []
        citation_paths: List[Dict[str, Any]] = []

        if use_graph and self._graph is not None:
            graph_facts.extend(self._entity_facts(query))
            if paper_id:
                graph_facts.extend(self._paper_facts(paper_id))

        if use_graph and query_type == QueryType.EVOLUTION and self._citations is not None and paper_id:
            citation_paths = self._citations.expand(paper_id)
            graph_facts.extend(self._citation_path_facts(paper_id, citation_paths))

        if use_vector and self._vector is not None:
            vector_results = self._vector.retrieve(
                query, top_k=top_k, paper_id=paper_id,
            )

        result = {
            "query": query,
            "query_type": query_type.value,
            "graph_facts": graph_facts,
            "vector_results": vector_results,
            "citation_paths": citation_paths,
            "source_paper_ids": self._collect_source_paper_ids(
                graph_facts, vector_results, citation_paths,
            ),
        }
        logger.info(
            "hybrid_retrieval",
            query=query,
            query_type=query_type.value,
            graph_facts=len(graph_facts),
            vector_results=len(vector_results),
            citation_paths=len(citation_paths),
        )
        return result

    # ------------------------------------------------------------------
    # Graph fact collection
    # ------------------------------------------------------------------

    def _entity_facts(self, query: str) -> List[Dict[str, Any]]:
        """Facts for every Method/Dataset/Task/Metric/... mentioned in the query."""
        entities = self._entity_extractor.extract(query)
        facts: List[Dict[str, Any]] = []
        seen = set()
        for ent in entities:
            key = (ent["type"], ent["name"].lower())
            if key in seen:
                continue
            seen.add(key)
            facts.extend(self._graph.get_entity_relations(ent["name"], ent["type"]))
        return facts

    def _paper_facts(self, paper_id: str) -> List[Dict[str, Any]]:
        """Citation and entity-mention facts anchored to a specific paper."""
        facts: List[Dict[str, Any]] = []
        facts.extend(self._graph.find_citing_papers(paper_id))
        facts.extend(self._graph.find_cited_papers(paper_id))
        facts.extend(self._graph.find_entities_for_paper(paper_id))
        return facts

    @staticmethod
    def _citation_path_facts(
        paper_id: str, citation_paths: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        facts = []
        for cp in citation_paths:
            cited_paper = {"name": cp.get("title") or cp["paper_id"], "type": "Paper", "paper_id": cp["paper_id"]}
            anchor_paper = {"name": paper_id, "type": "Paper", "paper_id": paper_id}
            if cp.get("direction") == "backward":
                subject, obj = cited_paper, anchor_paper
            else:
                subject, obj = anchor_paper, cited_paper
            facts.append({
                "subject": subject,
                "relation": "CITES",
                "object": obj,
                "evidence": None,
                "source_paper_ids": sorted({paper_id, cp["paper_id"]}),
            })
        return facts

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_source_paper_ids(
        graph_facts: List[Dict[str, Any]],
        vector_results: List[Dict[str, Any]],
        citation_paths: List[Dict[str, Any]],
    ) -> List[str]:
        ids = set()
        for fact in graph_facts:
            ids.update(fact.get("source_paper_ids") or [])
        for r in vector_results:
            if r.get("paper_id"):
                ids.add(r["paper_id"])
        for cp in citation_paths:
            ids.add(cp["paper_id"])
        return sorted(ids)
