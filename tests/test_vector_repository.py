"""
Tests for VectorRepository (Phase 9.3)

Covers:
- store_paper_chunks: embeds + upserts, re-ingestion safety (delete first)
- similarity_search: embeds query, returns results
- hybrid_search: with graph paper filter
- paper_search: aggregates by paper_id
- delete_paper_vectors
- Empty input handling
"""

import pytest
from unittest.mock import patch, MagicMock, call

from qdrant_client.http import models as qmodels


# ======================================================================
# store_paper_chunks
# ======================================================================

class TestStorePaperChunks:
    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_stores_chunks_and_returns_count(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_dim = 384
        mock_embed.embed.return_value = [[0.1] * 384, [0.2] * 384]
        mock_qdrant.upsert.return_value = 2

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        chunks = [
            {"text": "Hello world", "section": "Abstract", "page": 1},
            {"text": "Second chunk", "section": "Methods", "page": 3},
        ]
        result = repo.store_paper_chunks("paper-1", chunks)

        assert result["chunks_stored"] == 2
        mock_embed.embed.assert_called_once_with(["Hello world", "Second chunk"])
        mock_qdrant.upsert.assert_called_once()

        # Verify upserted points have correct structure
        points = mock_qdrant.upsert.call_args[0][1]
        assert len(points) == 2
        assert points[0].id == "paper-1__chunk_00000"
        assert points[1].id == "paper-1__chunk_00001"
        assert points[0].payload["paper_id"] == "paper-1"
        assert points[0].payload["section"] == "Abstract"
        assert points[0].payload["page"] == 1

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_deletes_existing_before_upsert(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_dim = 384
        mock_embed.embed.return_value = [[0.1] * 384]

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        chunks = [{"text": "new content"}]
        repo.store_paper_chunks("paper-1", chunks)

        # delete_points should be called before upsert
        delete_call = mock_qdrant.delete_points.call_args
        assert delete_call is not None
        assert delete_call[0][1] == "paper-1"

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_empty_chunks_returns_zero(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        result = repo.store_paper_chunks("paper-1", [])
        assert result["chunks_stored"] == 0
        mock_embed.embed.assert_not_called()

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_ensures_collection_on_store(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_dim = 768
        mock_embed.embed.return_value = [[0.1] * 768]

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        chunks = [{"text": "test"}]
        repo.store_paper_chunks("paper-1", chunks)

        mock_qdrant.ensure_collection.assert_called_with(
            collection_name="test-col", vector_dim=768
        )


# ======================================================================
# similarity_search
# ======================================================================

class TestSimilaritySearch:
    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_search_embeds_query_and_returns_results(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        mock_qdrant.search.return_value = [
            {"id": "p1", "score": 0.95, "payload": {"text": "match"}},
        ]

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        results = repo.similarity_search("machine learning", top_k=3)
        assert len(results) == 1
        assert results[0]["score"] == 0.95
        mock_embed.embed_single.assert_called_once_with("machine learning")
        mock_qdrant.search.assert_called_once()

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_search_passes_filters(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        mock_qdrant.search.return_value = []

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        repo.similarity_search("query", filters={"paper_id": "p1"})
        call_kwargs = mock_qdrant.search.call_args
        assert call_kwargs.kwargs["filters"] == {"paper_id": "p1"}

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_search_empty_query_vector_returns_empty(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = []

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        results = repo.similarity_search("query")
        assert results == []
        mock_qdrant.search.assert_not_called()


# ======================================================================
# hybrid_search
# ======================================================================

class TestHybridSearch:
    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_hybrid_passes_graph_paper_ids_as_filter(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        mock_qdrant.search.return_value = []

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        repo.hybrid_search(
            query="transformer attention",
            graph_paper_ids=["paper-a", "paper-b"],
            top_k=5,
        )

        call_kwargs = mock_qdrant.search.call_args
        assert call_kwargs.kwargs["filters"] == {"paper_id": ["paper-a", "paper-b"]}

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_hybrid_no_graph_ids_falls_back(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        mock_qdrant.search.return_value = []

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        repo.hybrid_search(query="test", graph_paper_ids=None)
        call_kwargs = mock_qdrant.search.call_args
        # No filter should be passed
        assert call_kwargs.kwargs["filters"] is None


# ======================================================================
# paper_search
# ======================================================================

class TestPaperSearch:
    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_paper_search_aggregates_by_paper(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        # Simulate 4 chunks: 2 from paper-a, 2 from paper-b
        mock_qdrant.search.return_value = [
            {"id": "c1", "score": 0.95, "payload": {"paper_id": "paper-a", "section": "Abstract", "text": "t1"}},
            {"id": "c2", "score": 0.90, "payload": {"paper_id": "paper-a", "section": "Methods", "text": "t2"}},
            {"id": "c3", "score": 0.85, "payload": {"paper_id": "paper-b", "section": "Abstract", "text": "t3"}},
            {"id": "c4", "score": 0.80, "payload": {"paper_id": "paper-b", "section": "Results", "text": "t4"}},
        ]

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        papers = repo.paper_search("attention mechanism", top_k=5)
        assert len(papers) == 2
        assert papers[0]["paper_id"] == "paper-a"  # higher best_score
        assert papers[0]["best_score"] == 0.95
        assert papers[0]["chunk_count"] == 2
        assert "Abstract" in papers[0]["sections"]
        assert "Methods" in papers[0]["sections"]

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_paper_search_empty_results(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value
        mock_embed.embed_single.return_value = [0.5] * 384
        mock_qdrant.search.return_value = []

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        assert repo.paper_search("nothing") == []


# ======================================================================
# delete_paper_vectors
# ======================================================================

class TestDeletePaperVectors:
    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_delete_calls_qdrant(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_embed = MockEmbedSvc.return_value

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        repo.delete_paper_vectors("paper-1")
        mock_qdrant.delete_points.assert_called_once_with("test-col", "paper-1")

    @patch("app.storage.vector_repository.QdrantClientWrapper")
    @patch("app.storage.vector_repository.EmbeddingService")
    def test_delete_handles_error_gracefully(self, MockEmbedSvc, MockQdrant):
        mock_qdrant = MockQdrant.return_value
        mock_qdrant.delete_points.side_effect = Exception("connection lost")
        mock_embed = MockEmbedSvc.return_value

        from app.storage.vector_repository import VectorRepository
        repo = VectorRepository(mock_qdrant, mock_embed, "test-col")

        # Should not raise
        repo.delete_paper_vectors("paper-1")