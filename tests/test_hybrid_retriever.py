"""Tests for HybridRetriever (Phase 13.2)."""

from unittest.mock import MagicMock

import pytest

from app.retrieval.hybrid_retriever import HybridRetriever
from app.retrieval.query_classifier import QueryType


def _make_retriever(graph=None, vector=None, citations=None):
    graph = graph if graph is not None else MagicMock()
    vector = vector if vector is not None else MagicMock()
    citations = citations if citations is not None else MagicMock()

    graph.get_entity_relations.return_value = []
    graph.find_citing_papers.return_value = []
    graph.find_cited_papers.return_value = []
    graph.find_entities_for_paper.return_value = []
    vector.retrieve.return_value = []
    citations.expand.return_value = []

    retriever = HybridRetriever(
        graph_retriever=graph, vector_retriever=vector, citation_expander=citations,
    )
    return retriever, graph, vector, citations


class TestRouting:
    def test_explanation_uses_vector_only(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("Explain how self-attention works.")

        assert result["query_type"] == QueryType.EXPLANATION.value
        vector.retrieve.assert_called_once()
        graph.get_entity_relations.assert_not_called()
        graph.find_citing_papers.assert_not_called()
        citations.expand.assert_not_called()

    def test_citation_uses_graph_only(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("Who cites this paper?", paper_id="p1")

        assert result["query_type"] == QueryType.CITATION.value
        graph.find_citing_papers.assert_called_once_with("p1")
        graph.find_cited_papers.assert_called_once_with("p1")
        graph.find_entities_for_paper.assert_called_once_with("p1")
        vector.retrieve.assert_not_called()

    def test_entity_lookup_uses_graph_only(self):
        retriever, graph, vector, citations = _make_retriever()

        retriever.retrieve("What is BERT?")

        graph.get_entity_relations.assert_called_with("BERT", "Method")
        vector.retrieve.assert_not_called()

    def test_comparison_uses_graph_and_vector(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("Compare BERT and GPT.")

        assert result["query_type"] == QueryType.COMPARISON.value
        assert graph.get_entity_relations.called
        vector.retrieve.assert_called_once()

    def test_survey_uses_graph_and_vector(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("Give a survey of Transformer methods.")

        assert result["query_type"] == QueryType.SURVEY.value
        assert graph.get_entity_relations.called
        vector.retrieve.assert_called_once()

    def test_evolution_uses_citation_expansion_and_graph_no_vector(self):
        retriever, graph, vector, citations = _make_retriever()
        citations.expand.return_value = [
            {"paper_id": "p0", "title": "Earlier Paper", "depth": 1,
             "path": ["p1", "p0"], "direction": "forward"},
        ]

        result = retriever.retrieve(
            "How did attention evolve from RNNs to Transformers?", paper_id="p1",
        )

        assert result["query_type"] == QueryType.EVOLUTION.value
        citations.expand.assert_called_once_with("p1")
        assert result["citation_paths"] == citations.expand.return_value
        vector.retrieve.assert_not_called()

    def test_evolution_without_paper_id_skips_citation_expansion(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("How did attention evolve over time?")

        citations.expand.assert_not_called()
        assert result["citation_paths"] == []


class TestGracefulDegradation:
    def test_missing_graph_retriever_does_not_raise(self):
        retriever = HybridRetriever(graph_retriever=None, vector_retriever=MagicMock())
        result = retriever.retrieve("Who cites this paper?", paper_id="p1")
        assert result["graph_facts"] == []

    def test_missing_vector_retriever_does_not_raise(self):
        retriever = HybridRetriever(graph_retriever=MagicMock(), vector_retriever=None)
        result = retriever.retrieve("Explain self-attention.")
        assert result["vector_results"] == []

    def test_missing_citation_expander_skips_evolution_expansion(self):
        graph = MagicMock()
        graph.get_entity_relations.return_value = []
        retriever = HybridRetriever(graph_retriever=graph, vector_retriever=None, citation_expander=None)
        result = retriever.retrieve("How did this evolve?", paper_id="p1")
        assert result["citation_paths"] == []


class TestSourcePaperIdAggregation:
    def test_aggregates_ids_from_graph_vector_and_citation_paths(self):
        retriever, graph, vector, citations = _make_retriever()
        graph.get_entity_relations.return_value = [
            {"subject": {"name": "GPT", "type": "Method", "paper_id": None},
             "relation": "IMPROVES_UPON", "object": {"name": "RNN", "type": "Method", "paper_id": None},
             "evidence": None, "source_paper_ids": ["paper-a"]},
        ]
        vector.retrieve.return_value = [
            {"id": "x", "score": 0.9, "text": "t", "paper_id": "paper-b",
             "section": None, "node_type": None, "node_name": None, "source_text": None, "page": None},
        ]

        result = retriever.retrieve("Compare GPT and RNN.")

        assert result["source_paper_ids"] == ["paper-a", "paper-b"]


class TestForceMode:
    def test_force_graph_skips_vector_even_for_explanation(self):
        retriever, graph, vector, citations = _make_retriever()

        retriever.retrieve("Explain how the Transformer works.", force_mode="graph")

        assert graph.get_entity_relations.called
        vector.retrieve.assert_not_called()

    def test_force_vector_skips_graph_even_for_citation(self):
        retriever, graph, vector, citations = _make_retriever()

        retriever.retrieve("Who cites this paper?", paper_id="p1", force_mode="vector")

        graph.get_entity_relations.assert_not_called()
        graph.find_citing_papers.assert_not_called()
        vector.retrieve.assert_called_once()

    def test_force_both_runs_graph_and_vector_for_any_type(self):
        retriever, graph, vector, citations = _make_retriever()

        retriever.retrieve("Who cites this paper?", paper_id="p1", force_mode="both")

        assert graph.find_citing_papers.called
        vector.retrieve.assert_called_once()

    def test_query_type_is_still_reported_under_force_mode(self):
        retriever, graph, vector, citations = _make_retriever()

        result = retriever.retrieve("Who cites this paper?", force_mode="vector")

        assert result["query_type"] == QueryType.CITATION.value


class TestEntityExtraction:
    def test_extracts_known_method_from_query_text(self):
        retriever, graph, vector, citations = _make_retriever()
        retriever.retrieve("What is BERT?")
        called_names = [c.args[0] for c in graph.get_entity_relations.call_args_list]
        assert "BERT" in called_names

    def test_deduplicates_repeated_entity_mentions(self):
        retriever, graph, vector, citations = _make_retriever()
        retriever.retrieve("Compare BERT with BERT variants.")
        # BERT should only trigger one get_entity_relations call despite two mentions.
        bert_calls = [c for c in graph.get_entity_relations.call_args_list if c.args[0] == "BERT"]
        assert len(bert_calls) == 1
