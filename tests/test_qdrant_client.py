"""
Tests for QdrantClientWrapper (Phase 9.2)

Covers:
- Collection lifecycle: create, exists, dimension mismatch recreate
- Upsert with batching
- Search with filters
- Delete by paper_id
- Health check
- Connection lifecycle (lazy init)
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse


# ======================================================================
# Helpers
# ======================================================================

def _make_mock_collection_info(dim=384, points=100):
    """Create a mock collection info object."""
    info = MagicMock()
    info.config.params.vectors.size = dim
    info.vectors_count = points
    info.points_count = points
    info.status = "green"
    return info


def _make_mock_hit(id="point-1", score=0.95, payload=None):
    hit = MagicMock()
    hit.id = id
    hit.score = score
    hit.payload = payload or {"paper_id": "paper-1", "text": "sample"}
    return hit


def _not_found(status_code=404, reason_phrase="Not Found"):
    """Build an UnexpectedResponse across qdrant-client versions (content/headers
    became required positional args in newer releases)."""
    return UnexpectedResponse(
        status_code=status_code,
        reason_phrase=reason_phrase,
        content=b"",
        headers={},
    )


# ======================================================================
# Collection management
# ======================================================================

class TestCollectionLifecycle:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_creates_collection_when_not_found(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.side_effect = _not_found()

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        created = client.ensure_collection("test-col", vector_dim=384)
        assert created is True
        mock_client.create_collection.assert_called_once()

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_skips_create_when_exists_same_dim(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.return_value = _make_mock_collection_info(dim=384)

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        created = client.ensure_collection("test-col", vector_dim=384)
        assert created is False
        mock_client.create_collection.assert_not_called()

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_recreates_on_dimension_mismatch(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.return_value = _make_mock_collection_info(dim=768)

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        created = client.ensure_collection("test-col", vector_dim=384)
        assert created is True
        mock_client.delete_collection.assert_called_once_with("test-col")
        mock_client.create_collection.assert_called_once()

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_delete_collection_noop_on_404(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.delete_collection.side_effect = _not_found()

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()
        client.delete_collection("nonexistent")  # should not raise

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_collection_info_returns_dict(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.return_value = _make_mock_collection_info(dim=384, points=42)

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        info = client.collection_info("test-col")
        assert info is not None
        assert info["dim"] == 384
        assert info["points_count"] == 42

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_collection_info_returns_none_on_404(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.side_effect = _not_found()

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        assert client.collection_info("nonexistent") is None


# ======================================================================
# Upsert
# ======================================================================

class TestUpsert:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_upsert_single_batch(self, MockDriver):
        mock_client = MockDriver.return_value
        points = [
            qmodels.PointStruct(id=f"p-{i}", vector=[0.1] * 384, payload={})
            for i in range(5)
        ]

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333", batch_size=10)
        client.connect()

        count = client.upsert("test-col", points)
        assert count == 5
        mock_client.upsert.assert_called_once()

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_upsert_multiple_batches(self, MockDriver):
        mock_client = MockDriver.return_value
        points = [
            qmodels.PointStruct(id=f"p-{i}", vector=[0.1] * 384, payload={})
            for i in range(5)
        ]

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333", batch_size=2)
        client.connect()

        count = client.upsert("test-col", points)
        assert count == 5
        # 5 points / batch_size 2 = 3 calls (2, 2, 1)
        assert mock_client.upsert.call_count == 3


# ======================================================================
# Search
# ======================================================================

class TestSearch:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_search_returns_dicts(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.search.return_value = [
            _make_mock_hit(id="h1", score=0.95),
            _make_mock_hit(id="h2", score=0.88),
        ]

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        results = client.search("test-col", [0.1] * 384, limit=5)
        assert len(results) == 2
        assert results[0]["score"] == 0.95
        assert "payload" in results[0]

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_search_with_filter(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.search.return_value = []

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        client.search(
            "test-col", [0.1] * 384, limit=5,
            filters={"paper_id": "p1"},
        )
        # Verify the filter was passed through
        call_kwargs = mock_client.search.call_args
        assert call_kwargs.kwargs.get("query_filter") is not None

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_search_batch(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.search_batch.return_value = [
            [_make_mock_hit(id="h1", score=0.9)],
            [_make_mock_hit(id="h2", score=0.8)],
        ]

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        results = client.search_batch(
            "test-col",
            [[0.1] * 384, [0.2] * 384],
            limit=3,
        )
        assert len(results) == 2
        assert len(results[0]) == 1


# ======================================================================
# Delete
# ======================================================================

class TestDelete:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_delete_points_calls_qdrant(self, MockDriver):
        mock_client = MockDriver.return_value

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        client.delete_points("test-col", "paper-123")
        mock_client.delete.assert_called_once()
        call_args = mock_client.delete.call_args
        selector = call_args.kwargs.get("points_selector")
        assert selector is not None


# ======================================================================
# Health check
# ======================================================================

class TestHealthCheck:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_health_check_success(self, MockDriver):
        mock_client = MockDriver.return_value

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        assert client.health_check() is True

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_health_check_failure(self, MockDriver):
        MockDriver.return_value.get_collections.side_effect = Exception("down")

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")

        assert client.health_check() is False


# ======================================================================
# Context manager
# ======================================================================

class TestContextManager:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_context_manager_calls_close(self, MockDriver):
        mock_client = MockDriver.return_value

        from app.storage.qdrant_client import QdrantClientWrapper
        with QdrantClientWrapper(url="http://localhost:6333") as client:
            assert client._client is not None

        mock_client.close.assert_called_once()
        assert client._client is None

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_close_handles_exception_gracefully(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.close.side_effect = Exception("connection reset")

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        # Should NOT raise — error is logged but swallowed
        client.close()
        assert client._client is None


# ======================================================================
# Dimension mismatch error handling
# ======================================================================

class TestDimensionMismatchErrors:
    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_delete_failure_during_recreate_raises_runtime_error(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.return_value = _make_mock_collection_info(dim=768)
        mock_client.delete_collection.side_effect = Exception("timeout")

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        with pytest.raises(RuntimeError, match="Cannot recreate collection"):
            client.ensure_collection("test-col", vector_dim=384)

    @patch("app.storage.qdrant_client.QdrantDriver")
    def test_create_failure_after_delete_raises_runtime_error(self, MockDriver):
        mock_client = MockDriver.return_value
        mock_client.get_collection.return_value = _make_mock_collection_info(dim=768)
        mock_client.create_collection.side_effect = Exception("server error")

        from app.storage.qdrant_client import QdrantClientWrapper
        client = QdrantClientWrapper(url="http://localhost:6333")
        client.connect()

        with pytest.raises(RuntimeError, match="was deleted .* but recreation"):
            client.ensure_collection("test-col", vector_dim=384)

        # Delete was called (collection is now gone), but create failed
        mock_client.delete_collection.assert_called_once_with("test-col")