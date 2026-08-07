"""Tests for VectorRetriever (Phase 12)."""

from unittest.mock import MagicMock

import pytest

from app.retrieval.vector_retriever import VectorRetriever


def _make_retriever(hits):
    repo = MagicMock()
    repo.similarity_search.return_value = hits
    return VectorRetriever(repo), repo


SAMPLE_HIT = {
    "id": "paper-1__chunk_00000",
    "score": 0.92,
    "payload": {
        "paper_id": "paper-1",
        "text": "The Transformer relies on self-attention.",
        "section": "Abstract",
        "node_type": "Method",
        "node_name": "Transformer",
        "source_text": "The Transformer relies on self-attention.",
        "page": 1,
    },
}


class TestRetrieve:
    def test_returns_normalized_results(self):
        retriever, repo = _make_retriever([SAMPLE_HIT])

        results = retriever.retrieve("what is the transformer")

        assert len(results) == 1
        r = results[0]
        assert r["id"] == "paper-1__chunk_00000"
        assert r["score"] == 0.92
        assert r["text"] == "The Transformer relies on self-attention."
        assert r["paper_id"] == "paper-1"
        assert r["section"] == "Abstract"
        assert r["node_type"] == "Method"
        assert r["node_name"] == "Transformer"

    def test_passes_top_k_and_no_filters_by_default(self):
        retriever, repo = _make_retriever([])
        retriever.retrieve("query", top_k=7)

        repo.similarity_search.assert_called_once_with(
            query="query", top_k=7, filters=None
        )

    def test_filters_by_paper_id(self):
        retriever, repo = _make_retriever([])
        retriever.retrieve("query", paper_id="paper-1")

        call_kwargs = repo.similarity_search.call_args.kwargs
        assert call_kwargs["filters"] == {"paper_id": "paper-1"}

    def test_filters_by_paper_id_list(self):
        retriever, repo = _make_retriever([])
        retriever.retrieve("query", paper_id=["paper-1", "paper-2"])

        call_kwargs = repo.similarity_search.call_args.kwargs
        assert call_kwargs["filters"] == {"paper_id": ["paper-1", "paper-2"]}

    def test_filters_by_node_type(self):
        retriever, repo = _make_retriever([])
        retriever.retrieve("query", node_type="Claim")

        call_kwargs = repo.similarity_search.call_args.kwargs
        assert call_kwargs["filters"] == {"node_type": "Claim"}

    def test_combined_filters(self):
        retriever, repo = _make_retriever([])
        retriever.retrieve("query", paper_id="paper-1", node_type="Method")

        call_kwargs = repo.similarity_search.call_args.kwargs
        assert call_kwargs["filters"] == {"paper_id": "paper-1", "node_type": "Method"}

    def test_empty_query_returns_empty_without_calling_repo(self):
        retriever, repo = _make_retriever([])
        assert retriever.retrieve("") == []
        assert retriever.retrieve("   ") == []
        repo.similarity_search.assert_not_called()

    def test_missing_payload_fields_default_to_none(self):
        retriever, repo = _make_retriever([{"id": "x", "score": 0.5, "payload": {}}])
        results = retriever.retrieve("query")
        assert results[0]["text"] is None
        assert results[0]["paper_id"] is None
        assert results[0]["node_type"] is None
