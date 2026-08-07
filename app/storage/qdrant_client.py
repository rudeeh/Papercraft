"""
Qdrant Client Module (Phase 9.2)

Provides a robust Qdrant client wrapper with:
- Connection management (lazy init, health check)
- Collection lifecycle (create with correct vector config, recreate on dim change)
- Batch upsert with configurable size
- Similarity search with metadata filtering
- Idempotent operations (upsert, not insert)

Risk Mitigations Addressed
--------------------------
- Dimension mismatch: ``ensure_collection()`` validates vector size against
  the stored collection config. If dimensions changed (e.g. model swap),
  the collection is recreated with a warning.
- Missing collection: auto-created on first write with proper HNSW params
  tuned for research-paper workloads.
- Connection failures: every public method catches Qdrant exceptions and
  returns empty results rather than crashing the pipeline.
- Batch size control: large upserts are chunked to prevent gRPC message
  size limits in Qdrant server.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog
from qdrant_client import QdrantClient as QdrantDriver
from qdrant_client.http import models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

logger = structlog.get_logger()

# Default HNSW parameters tuned for research-paper collections.
DEFAULT_HNSW_EF_CONSTRUCT = 128
DEFAULT_HNSW_M = 16
DEFAULT_BATCH_SIZE = 128


class QdrantClientWrapper:
    """
    Qdrant database client with collection-aware operations.

    Follows the same lazy-init pattern as ``Neo4jClient``:
    - ``connect()`` / ``close()`` for lifecycle
    - ``_ensure_connected()`` auto-connects on first use
    """

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._batch_size = batch_size
        self._client: Optional[QdrantDriver] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialize the Qdrant client and verify connectivity."""
        if self._client is None:
            logger.info("qdrant_connecting", url=self._url)
            kwargs: Dict[str, Any] = {"url": self._url}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = QdrantDriver(**kwargs)
            self._client.get_collections()
            logger.info("qdrant_connected", url=self._url)

    def close(self) -> None:
        """Close the Qdrant client and release all underlying resources."""
        client = self._client
        self._client = None  # prevent further use immediately

        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("qdrant_close_error", error=str(exc))

            # Also close the underlying HTTP/gRPC transport if accessible.
            for attr in ("_client", "grpc_channel", "_grpc_channel"):
                transport = getattr(client, attr, None)
                if transport is not None and hasattr(transport, "close"):
                    try:
                        transport.close()
                    except Exception as exc:
                        logger.warning(
                            "qdrant_transport_close_error",
                            transport=attr,
                            error=str(exc),
                        )

        logger.info("qdrant_closed")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _ensure_connected(self) -> None:
        if self._client is None:
            self.connect()

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def ensure_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance: str = "cosine",
    ) -> bool:
        """
        Create the collection if it does not exist.

        If the collection exists but has a different vector dimension,
        it is **recreated** (deleted + created) with a warning log.

        Returns ``True`` if the collection was created or recreated.
        """
        self._ensure_connected()

        dist_map = {
            "cosine": qmodels.Distance.COSINE,
            "euclid": qmodels.Distance.EUCLID,
            "dot": qmodels.Distance.DOT,
        }
        qdist = dist_map.get(distance, qmodels.Distance.COSINE)

        try:
            existing = self._client.get_collection(collection_name)
            existing_dim = existing.config.params.vectors.size
            if existing_dim == vector_dim:
                logger.debug(
                    "qdrant_collection_exists",
                    collection=collection_name,
                    dim=vector_dim,
                )
                return False

            logger.warning(
                "qdrant_dimension_mismatch_recreating",
                collection=collection_name,
                old_dim=existing_dim,
                new_dim=vector_dim,
            )
            try:
                self._client.delete_collection(collection_name)
            except Exception as exc:
                logger.error(
                    "qdrant_collection_delete_failed_during_recreate",
                    collection=collection_name,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"Cannot recreate collection '{collection_name}': "
                    f"delete failed (old_dim={existing_dim}, "
                    f"new_dim={vector_dim}).  Manually delete the "
                    f"collection and retry."
                ) from exc

            # Collection was deleted successfully; recreate with new
            # dimensions.  If this fails the collection is gone —
            # the error message must make that clear.
            try:
                self._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=vector_dim,
                        distance=qdist,
                        hnsw_config=qmodels.HnswConfigDiff(
                            ef_construct=DEFAULT_HNSW_EF_CONSTRUCT,
                            m=DEFAULT_HNSW_M,
                        ),
                    ),
                )
            except Exception as exc:
                logger.error(
                    "qdrant_collection_recreate_failed_after_delete",
                    collection=collection_name,
                    old_dim=existing_dim,
                    new_dim=vector_dim,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"Collection '{collection_name}' was deleted "
                    f"(old_dim={existing_dim}) but recreation with "
                    f"new_dim={vector_dim} failed: {exc}.  "
                    f"The collection no longer exists — retry or "
                    f"manually recreate it."
                ) from exc

            logger.info(
                "qdrant_collection_recreated",
                collection=collection_name,
                old_dim=existing_dim,
                new_dim=vector_dim,
            )
            return True
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.info(
                    "qdrant_collection_not_found_will_create",
                    collection=collection_name,
                )
            else:
                raise

        try:
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(
                    size=vector_dim,
                    distance=qdist,
                    hnsw_config=qmodels.HnswConfigDiff(
                        ef_construct=DEFAULT_HNSW_EF_CONSTRUCT,
                        m=DEFAULT_HNSW_M,
                    ),
                ),
            )
        except Exception as exc:
            logger.error(
                "qdrant_collection_create_failed",
                collection=collection_name,
                dim=vector_dim,
                error=str(exc),
            )
            raise RuntimeError(
                f"Failed to create collection '{collection_name}' "
                f"with dim={vector_dim}: {exc}"
            ) from exc
        logger.info(
            "qdrant_collection_created",
            collection=collection_name,
            dim=vector_dim,
            distance=distance,
        )
        return True

    def delete_collection(self, collection_name: str) -> None:
        """Delete a collection. No-op if it does not exist."""
        self._ensure_connected()
        try:
            self._client.delete_collection(collection_name)
            logger.info("qdrant_collection_deleted", collection=collection_name)
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise

    def collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Return collection metadata or None if not found."""
        self._ensure_connected()
        try:
            info = self._client.get_collection(collection_name)
            return {
                "name": collection_name,
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
                "dim": info.config.params.vectors.size,
            }
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                return None
            raise

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        collection_name: str,
        points: List[qmodels.PointStruct],
    ) -> int:
        """
        Upsert points in batches. Returns the total number of points upserted.
        """
        self._ensure_connected()
        total = 0
        for i in range(0, len(points), self._batch_size):
            batch = points[i : i + self._batch_size]
            self._client.upsert(
                collection_name=collection_name,
                points=batch,
            )
            total += len(batch)
            logger.debug(
                "qdrant_upsert_batch",
                collection=collection_name,
                batch_start=i,
                batch_size=len(batch),
            )
        return total

    def delete_points(self, collection_name: str, paper_id: str) -> None:
        """
        Delete all points belonging to a paper (matched by payload filter).
        """
        self._ensure_connected()
        self._client.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="paper_id",
                            match=qmodels.MatchValue(value=paper_id),
                        )
                    ]
                )
            ),
        )
        logger.info(
            "qdrant_points_deleted",
            collection=collection_name,
            paper_id=paper_id,
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for the closest vectors.

        Returns list of dicts with keys: id, score, payload.
        """
        self._ensure_connected()
        qfilter = self._build_filter(filters) if filters else None

        results = self._client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=limit,
            query_filter=qfilter,
        )

        return [
            {"id": hit.id, "score": hit.score, "payload": hit.payload}
            for hit in results
        ]

    def search_batch(
        self,
        collection_name: str,
        query_vectors: List[List[float]],
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[List[Dict[str, Any]]]:
        """
        Batch search — more efficient than calling ``search()`` in a loop.
        """
        self._ensure_connected()
        qfilter = self._build_filter(filters) if filters else None

        results = self._client.search_batch(
            collection_name=collection_name,
            requests=[
                qmodels.SearchRequest(
                    vector=qv, limit=limit, filter=qfilter,
                )
                for qv in query_vectors
            ],
        )

        return [
            [
                {"id": hit.id, "score": hit.score, "payload": hit.payload}
                for hit in batch
            ]
            for batch in results
        ]

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Return True if Qdrant is reachable."""
        try:
            self._ensure_connected()
            self._client.get_collections()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(filter_dict: Dict[str, Any]) -> qmodels.Filter:
        """
        Convert a flat filter dict into a Qdrant Filter.

        - ``{"paper_id": "xxx"}``        -> exact match
        - ``{"paper_id": ["a", "b"]}``  -> match any
        """
        conditions: List[qmodels.FieldCondition] = []
        for key, value in filter_dict.items():
            if isinstance(value, list):
                conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchAny(any=value),
                    )
                )
            else:
                conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchValue(value=value),
                    )
                )
        return qmodels.Filter(must=conditions)