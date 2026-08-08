"""
Tests for WeaviateClientWrapper (vector-DB swap: Qdrant -> Weaviate)

Covers:
- Collection name normalization (Weaviate class-name rules)
- Collection lifecycle: create, exists, dimension mismatch recreate
- Upsert with batching and failure counting
- Search result mapping (distance -> similarity score, payload cleanup)
- Delete by paper_id
- Health check
- Connection lifecycle (lazy init, context manager)
- Filter building (equal / contains_any / all_of)
"""

import pytest
from unittest.mock import patch, MagicMock

from app.storage.weaviate_client import (
    VectorPoint,
    WeaviateClientWrapper,
    normalize_collection_name,
)


# ======================================================================
# Helpers
# ======================================================================

def _make_client(mock_connect, batch_size=128, **kwargs):
    """Build a wrapper wired to a MagicMock weaviate client."""
    mock_driver = MagicMock()
    mock_driver.is_ready.return_value = True
    mock_connect.return_value = mock_driver
    wrapper = WeaviateClientWrapper(
        url="http://localhost:8080", batch_size=batch_size, **kwargs
    )
    return wrapper, mock_driver


def _make_search_obj(uuid="00000000-0000-0000-0000-000000000001",
                     distance=0.25, properties=None):
    obj = MagicMock()
    obj.uuid = uuid
    obj.metadata.distance = distance
    obj.properties = properties if properties is not None else {
        "paper_id": "paper-1", "text": "sample", "section": None,
    }
    return obj


# ======================================================================
# Collection name normalization
# ======================================================================

class TestNormalizeCollectionName:
    def test_capitalizes_first_letter(self):
        assert normalize_collection_name("documents") == "Documents"

    def test_already_valid_name_kept(self):
        assert normalize_collection_name("Documents") == "Documents"

    def test_invalid_chars_replaced(self):
        assert normalize_collection_name("test-col") == "Test_col"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            normalize_collection_name("")


# ======================================================================
# Connection lifecycle
# ======================================================================

class TestConnectionLifecycle:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_lazy_init_no_connect_on_construction(self, mock_connect):
        WeaviateClientWrapper(url="http://localhost:8080")
        mock_connect.assert_not_called()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_first_operation_connects(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        driver.collections.get.return_value.config.get.return_value.description = (
            "dim=384;distance=cosine"
        )
        wrapper.ensure_collection("documents", vector_dim=384)
        mock_connect.assert_called_once()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_not_ready_raises(self, mock_connect):
        mock_driver = MagicMock()
        mock_driver.is_ready.return_value = False
        mock_connect.return_value = mock_driver
        wrapper = WeaviateClientWrapper(url="http://localhost:8080")
        with pytest.raises(RuntimeError):
            wrapper.connect()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_context_manager_connects_and_closes(self, mock_connect):
        mock_driver = MagicMock()
        mock_driver.is_ready.return_value = True
        mock_connect.return_value = mock_driver
        with WeaviateClientWrapper(url="http://localhost:8080") as wrapper:
            assert wrapper._client is not None
        mock_driver.close.assert_called_once()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_grpc_port_and_url_parsing(self, mock_connect):
        mock_driver = MagicMock()
        mock_driver.is_ready.return_value = True
        mock_connect.return_value = mock_driver
        wrapper = WeaviateClientWrapper(
            url="http://weaviate:8080", grpc_port=50051
        )
        wrapper.connect()
        kwargs = mock_connect.call_args.kwargs
        assert kwargs["http_host"] == "weaviate"
        assert kwargs["http_port"] == 8080
        assert kwargs["http_secure"] is False
        assert kwargs["grpc_port"] == 50051


# ======================================================================
# Collection management
# ======================================================================

class TestCollectionLifecycle:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_creates_collection_when_not_found(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = False

        created = wrapper.ensure_collection("documents", vector_dim=384)

        assert created is True
        driver.collections.create.assert_called_once()
        assert driver.collections.create.call_args.kwargs["name"] == "Documents"
        assert "dim=384" in driver.collections.create.call_args.kwargs["description"]

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_existing_collection_matching_dim_untouched(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        driver.collections.get.return_value.config.get.return_value.description = (
            "dim=384;distance=cosine"
        )

        created = wrapper.ensure_collection("documents", vector_dim=384)

        assert created is False
        driver.collections.create.assert_not_called()
        driver.collections.delete.assert_not_called()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_dimension_mismatch_recreates(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        driver.collections.get.return_value.config.get.return_value.description = (
            "dim=384;distance=cosine"
        )

        created = wrapper.ensure_collection("documents", vector_dim=768)

        assert created is True
        driver.collections.delete.assert_called_once_with("Documents")
        driver.collections.create.assert_called_once()
        assert "dim=768" in driver.collections.create.call_args.kwargs["description"]

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_unknown_stored_dim_is_tolerated(self, mock_connect):
        """Collections without a dim marker (e.g. hand-created) are left alone."""
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        driver.collections.get.return_value.config.get.return_value.description = ""

        created = wrapper.ensure_collection("documents", vector_dim=384)

        assert created is False
        driver.collections.delete.assert_not_called()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_collection_info_missing_returns_none(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = False
        assert wrapper.collection_info("documents") is None

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_collection_info_returns_counts_and_dim(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        collection = driver.collections.get.return_value
        collection.aggregate.over_all.return_value.total_count = 42
        collection.config.get.return_value.description = "dim=384;distance=cosine"

        info = wrapper.collection_info("documents")

        assert info["name"] == "Documents"
        assert info["points_count"] == 42
        assert info["dim"] == 384


# ======================================================================
# Upsert
# ======================================================================

class TestUpsert:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_upsert_adds_all_points(self, mock_connect):
        wrapper, driver = _make_client(mock_connect, batch_size=2)
        collection = driver.collections.get.return_value
        collection.batch.failed_objects = []
        batch_ctx = collection.batch.fixed_size.return_value.__enter__.return_value

        points = [
            VectorPoint(id=f"0000000{i}-0000-0000-0000-000000000000",
                        vector=[0.1, 0.2], payload={"paper_id": "p1"})
            for i in range(5)
        ]
        total = wrapper.upsert("documents", points)

        assert total == 5
        assert batch_ctx.add_object.call_count == 5
        first = batch_ctx.add_object.call_args_list[0].kwargs
        assert first["uuid"] == points[0].id
        assert first["vector"] == [0.1, 0.2]
        assert first["properties"] == {"paper_id": "p1"}

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_upsert_counts_failures(self, mock_connect):
        wrapper, driver = _make_client(mock_connect, batch_size=10)
        collection = driver.collections.get.return_value
        failed = MagicMock()
        failed.message = "boom"
        collection.batch.failed_objects = [failed]

        points = [
            VectorPoint(id=f"0000000{i}-0000-0000-0000-000000000000",
                        vector=[0.1], payload={})
            for i in range(3)
        ]
        total = wrapper.upsert("documents", points)

        assert total == 2  # 3 sent, 1 failed


# ======================================================================
# Search
# ======================================================================

class TestSearch:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_search_maps_distance_to_score_and_cleans_payload(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        collection = driver.collections.get.return_value
        response = MagicMock()
        response.objects = [_make_search_obj(distance=0.25)]
        collection.query.near_vector.return_value = response

        results = wrapper.search("documents", query_vector=[0.1] * 4, limit=5)

        assert len(results) == 1
        assert results[0]["score"] == pytest.approx(0.75)
        assert results[0]["payload"] == {"paper_id": "paper-1", "text": "sample"}
        # None-valued properties are stripped
        assert "section" not in results[0]["payload"]

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_search_missing_collection_returns_empty(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = False
        assert wrapper.search("documents", query_vector=[0.1]) == []

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_search_passes_limit_and_filters(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        collection = driver.collections.get.return_value
        collection.query.near_vector.return_value.objects = []

        wrapper.search(
            "documents", query_vector=[0.1], limit=7,
            filters={"paper_id": "p1"},
        )

        kwargs = collection.query.near_vector.call_args.kwargs
        assert kwargs["limit"] == 7
        assert kwargs["filters"] is not None

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_search_batch_loops_per_vector(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        collection = driver.collections.get.return_value
        collection.query.near_vector.return_value.objects = []

        results = wrapper.search_batch("documents", [[0.1], [0.2], [0.3]])

        assert len(results) == 3
        assert collection.query.near_vector.call_count == 3


# ======================================================================
# Delete
# ======================================================================

class TestDelete:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_delete_points_filters_by_paper_id(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = True
        collection = driver.collections.get.return_value

        wrapper.delete_points("documents", "paper-1")

        collection.data.delete_many.assert_called_once()
        assert "where" in collection.data.delete_many.call_args.kwargs

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_delete_points_missing_collection_noop(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.exists.return_value = False
        wrapper.delete_points("documents", "paper-1")  # must not raise
        driver.collections.get.assert_not_called()

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_delete_collection_swallows_errors(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        driver.collections.delete.side_effect = Exception("nope")
        wrapper.delete_collection("documents")  # must not raise


# ======================================================================
# Health check
# ======================================================================

class TestHealthCheck:
    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_healthy(self, mock_connect):
        wrapper, driver = _make_client(mock_connect)
        assert wrapper.health_check() is True

    @patch("app.storage.weaviate_client.weaviate.connect_to_custom")
    def test_unreachable_returns_false(self, mock_connect):
        mock_connect.side_effect = Exception("connection refused")
        wrapper = WeaviateClientWrapper(url="http://localhost:9999")
        assert wrapper.health_check() is False


# ======================================================================
# Filter building
# ======================================================================

class TestBuildFilter:
    @patch("app.storage.weaviate_client.Filter")
    def test_single_value_equal(self, MockFilter):
        WeaviateClientWrapper._build_filter({"paper_id": "p1"})
        MockFilter.by_property.assert_called_once_with("paper_id")
        MockFilter.by_property.return_value.equal.assert_called_once_with("p1")
        MockFilter.all_of.assert_not_called()

    @patch("app.storage.weaviate_client.Filter")
    def test_list_value_contains_any(self, MockFilter):
        WeaviateClientWrapper._build_filter({"paper_id": ["a", "b"]})
        MockFilter.by_property.return_value.contains_any.assert_called_once_with(
            ["a", "b"]
        )

    @patch("app.storage.weaviate_client.Filter")
    def test_multiple_conditions_combined_with_all_of(self, MockFilter):
        WeaviateClientWrapper._build_filter(
            {"paper_id": "p1", "section": "Abstract"}
        )
        MockFilter.all_of.assert_called_once()
        assert len(MockFilter.all_of.call_args[0][0]) == 2
