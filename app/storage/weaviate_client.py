"""
Weaviate Client Module (Papercraft vector-DB decision, ADR: Weaviate)

Drop-in replacement for the former ``QdrantClientWrapper`` — same public
surface, same semantics — backed by Weaviate 1.28+ via the v4 Python
client (REST + gRPC).

Provides:
- Connection management (lazy init, health check)
- Collection lifecycle (create with correct vector config, recreate on
  dim change)
- Batch upsert with configurable size
- Similarity search with metadata filtering
- Idempotent operations (deterministic UUIDs upsert-overwrite)

Semantics preserved from the Qdrant wrapper
-------------------------------------------
- ``ensure_collection()`` validates vector dimension. Weaviate does not
  store an explicit dimension for self-provided vectors, so the dim is
  recorded in the collection *description* (``dim=384``) at creation and
  compared on every call; mismatch triggers delete + recreate with a
  warning (model-swap safety).
- ``search()`` returns ``{"id", "score", "payload"}`` dicts where score
  is a similarity (higher = better). Weaviate reports *distance*, so the
  score is ``1.0 - distance`` — for cosine this equals the same cosine
  similarity Qdrant returned.
- ``upsert()`` takes ``VectorPoint`` objects (id, vector, payload) — the
  neutral equivalent of Qdrant's ``PointStruct``. Deterministic UUIDs +
  Weaviate's PUT batch semantics make re-ingestion idempotent.
- Collection names are normalized to valid Weaviate class names
  (``documents`` -> ``Documents``); callers keep using the config value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import structlog
import weaviate
from weaviate.classes.config import Configure, DataType, Property, VectorDistances
from weaviate.classes.init import Auth
from weaviate.classes.query import Filter, MetadataQuery

logger = structlog.get_logger()

# HNSW parameters carried over from the Qdrant setup (tuned for
# research-paper collections).
DEFAULT_HNSW_EF_CONSTRUCT = 128
DEFAULT_HNSW_M = 16
DEFAULT_BATCH_SIZE = 128

# Explicit property schema for paper-chunk payloads. Declaring these up
# front avoids auto-schema type inference surprises; auto-schema still
# covers any extra keys.
_CHUNK_PROPERTIES = [
    ("paper_id", DataType.TEXT),
    ("text", DataType.TEXT),
    ("chunk_id", DataType.TEXT),
    ("section", DataType.TEXT),
    ("page", DataType.INT),
    ("chunk_index", DataType.INT),
    ("node_type", DataType.TEXT),
    ("node_name", DataType.TEXT),
    ("source_text", DataType.TEXT),
]

_DIM_RE = re.compile(r"dim=(\d+)")


@dataclass
class VectorPoint:
    """Storage-agnostic point: deterministic UUID string, vector, payload."""

    id: str
    vector: List[float]
    payload: Dict[str, Any] = field(default_factory=dict)


def normalize_collection_name(name: str) -> str:
    """
    Weaviate class names must match ``[A-Z][_0-9A-Za-z]*``. Replace
    invalid characters with ``_`` and capitalize the first letter, so
    config values like ``documents`` map to class ``Documents``.
    """
    cleaned = re.sub(r"[^_0-9A-Za-z]", "_", name)
    if not cleaned:
        raise ValueError(f"Cannot derive a Weaviate class name from {name!r}")
    return cleaned[0].upper() + cleaned[1:]


class WeaviateClientWrapper:
    """
    Weaviate database client with collection-aware operations.

    Follows the same lazy-init pattern as ``Neo4jClient``:
    - ``connect()`` / ``close()`` for lifecycle
    - ``_ensure_connected()`` auto-connects on first use
    """

    def __init__(
        self,
        url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        grpc_port: int = 50051,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._grpc_port = grpc_port
        self._batch_size = batch_size
        self._client: Optional[weaviate.WeaviateClient] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialize the Weaviate client and verify connectivity."""
        if self._client is None:
            logger.info("weaviate_connecting", url=self._url)
            parsed = urlparse(self._url)
            host = parsed.hostname or "localhost"
            secure = parsed.scheme == "https"
            port = parsed.port or (443 if secure else 8080)

            kwargs: Dict[str, Any] = {}
            if self._api_key:
                kwargs["auth_credentials"] = Auth.api_key(self._api_key)

            self._client = weaviate.connect_to_custom(
                http_host=host,
                http_port=port,
                http_secure=secure,
                grpc_host=host,
                grpc_port=self._grpc_port,
                grpc_secure=secure,
                **kwargs,
            )
            if not self._client.is_ready():
                raise RuntimeError(f"Weaviate at {self._url} is not ready")
            logger.info("weaviate_connected", url=self._url)

    def close(self) -> None:
        """Close the Weaviate client and release underlying transports."""
        client = self._client
        self._client = None  # prevent further use immediately

        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("weaviate_close_error", error=str(exc))

        logger.info("weaviate_closed")

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

    def _create_collection(
        self, class_name: str, vector_dim: int, distance: str
    ) -> None:
        dist_map = {
            "cosine": VectorDistances.COSINE,
            "euclid": VectorDistances.L2_SQUARED,
            "dot": VectorDistances.DOT,
        }
        wdist = dist_map.get(distance, VectorDistances.COSINE)

        self._client.collections.create(
            name=class_name,
            description=f"dim={vector_dim};distance={distance}",
            vector_config=Configure.Vectors.self_provided(
                vector_index_config=Configure.VectorIndex.hnsw(
                    distance_metric=wdist,
                    ef_construction=DEFAULT_HNSW_EF_CONSTRUCT,
                    max_connections=DEFAULT_HNSW_M,
                ),
            ),
            properties=[
                Property(name=n, data_type=t) for n, t in _CHUNK_PROPERTIES
            ],
        )

    def _stored_dim(self, class_name: str) -> Optional[int]:
        """Read the dimension recorded in the collection description."""
        config = self._client.collections.get(class_name).config.get()
        match = _DIM_RE.search(config.description or "")
        return int(match.group(1)) if match else None

    def ensure_collection(
        self,
        collection_name: str,
        vector_dim: int,
        distance: str = "cosine",
    ) -> bool:
        """
        Create the collection if it does not exist.

        If the collection exists but was created for a different vector
        dimension, it is **recreated** (deleted + created) with a warning.

        Returns ``True`` if the collection was created or recreated.
        """
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)

        if self._client.collections.exists(class_name):
            existing_dim = self._stored_dim(class_name)
            if existing_dim == vector_dim or existing_dim is None:
                logger.debug(
                    "weaviate_collection_exists",
                    collection=class_name,
                    dim=vector_dim,
                )
                return False

            logger.warning(
                "weaviate_dimension_mismatch_recreating",
                collection=class_name,
                old_dim=existing_dim,
                new_dim=vector_dim,
            )
            try:
                self._client.collections.delete(class_name)
            except Exception as exc:
                logger.error(
                    "weaviate_collection_delete_failed_during_recreate",
                    collection=class_name,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"Cannot recreate collection '{class_name}': delete "
                    f"failed (old_dim={existing_dim}, new_dim={vector_dim}). "
                    f"Manually delete the collection and retry."
                ) from exc

            try:
                self._create_collection(class_name, vector_dim, distance)
            except Exception as exc:
                logger.error(
                    "weaviate_collection_recreate_failed_after_delete",
                    collection=class_name,
                    old_dim=existing_dim,
                    new_dim=vector_dim,
                    error=str(exc),
                )
                raise RuntimeError(
                    f"Collection '{class_name}' was deleted "
                    f"(old_dim={existing_dim}) but recreation with "
                    f"new_dim={vector_dim} failed: {exc}. The collection "
                    f"no longer exists — retry or manually recreate it."
                ) from exc

            logger.info(
                "weaviate_collection_recreated",
                collection=class_name,
                old_dim=existing_dim,
                new_dim=vector_dim,
            )
            return True

        try:
            self._create_collection(class_name, vector_dim, distance)
        except Exception as exc:
            logger.error(
                "weaviate_collection_create_failed",
                collection=class_name,
                dim=vector_dim,
                error=str(exc),
            )
            raise RuntimeError(
                f"Failed to create collection '{class_name}' "
                f"with dim={vector_dim}: {exc}"
            ) from exc

        logger.info(
            "weaviate_collection_created",
            collection=class_name,
            dim=vector_dim,
            distance=distance,
        )
        return True

    def delete_collection(self, collection_name: str) -> None:
        """Delete a collection. No-op if it does not exist."""
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)
        try:
            self._client.collections.delete(class_name)
            logger.info("weaviate_collection_deleted", collection=class_name)
        except Exception as exc:
            logger.warning(
                "weaviate_collection_delete_skipped",
                collection=class_name,
                error=str(exc),
            )

    def collection_info(self, collection_name: str) -> Optional[Dict[str, Any]]:
        """Return collection metadata or None if not found."""
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)
        if not self._client.collections.exists(class_name):
            return None

        collection = self._client.collections.get(class_name)
        count = collection.aggregate.over_all(total_count=True).total_count
        return {
            "name": class_name,
            "vectors_count": count,
            "points_count": count,
            "status": "green",
            "dim": self._stored_dim(class_name),
        }

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        collection_name: str,
        points: List[VectorPoint],
    ) -> int:
        """
        Upsert points in batches. Returns the number of points accepted.

        Weaviate's batch uses PUT semantics per UUID, so re-sending the
        same deterministic IDs overwrites in place (idempotent
        re-ingestion, matching the Qdrant behaviour).
        """
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)
        collection = self._client.collections.get(class_name)

        total = 0
        for i in range(0, len(points), self._batch_size):
            chunk = points[i : i + self._batch_size]
            with collection.batch.fixed_size(batch_size=self._batch_size) as batch:
                for point in chunk:
                    batch.add_object(
                        uuid=point.id,
                        vector=point.vector,
                        properties=point.payload,
                    )
            failed = collection.batch.failed_objects
            if failed:
                logger.error(
                    "weaviate_upsert_batch_failures",
                    collection=class_name,
                    batch_start=i,
                    failed=len(failed),
                    first_error=str(failed[0].message)[:200],
                )
            total += len(chunk) - len(failed)
            logger.debug(
                "weaviate_upsert_batch",
                collection=class_name,
                batch_start=i,
                batch_size=len(chunk),
            )
        return total

    def delete_points(self, collection_name: str, paper_id: str) -> None:
        """
        Delete all points belonging to a paper (matched by payload filter).
        """
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)
        if not self._client.collections.exists(class_name):
            return
        collection = self._client.collections.get(class_name)
        collection.data.delete_many(
            where=Filter.by_property("paper_id").equal(paper_id)
        )
        logger.info(
            "weaviate_points_deleted",
            collection=class_name,
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

        Returns list of dicts with keys: id, score, payload. Score is a
        similarity (``1.0 - distance``; for cosine collections this is
        the cosine similarity, matching the old Qdrant scores).
        """
        self._ensure_connected()
        class_name = normalize_collection_name(collection_name)
        if not self._client.collections.exists(class_name):
            return []
        collection = self._client.collections.get(class_name)

        wfilter = self._build_filter(filters) if filters else None
        response = collection.query.near_vector(
            near_vector=query_vector,
            limit=limit,
            filters=wfilter,
            return_metadata=MetadataQuery(distance=True),
        )

        results: List[Dict[str, Any]] = []
        for obj in response.objects:
            distance = obj.metadata.distance
            score = (1.0 - distance) if distance is not None else 0.0
            payload = {
                k: v for k, v in (obj.properties or {}).items() if v is not None
            }
            results.append(
                {"id": str(obj.uuid), "score": score, "payload": payload}
            )
        return results

    def search_batch(
        self,
        collection_name: str,
        query_vectors: List[List[float]],
        limit: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[List[Dict[str, Any]]]:
        """
        Batch search. Weaviate has no multi-vector query endpoint, so this
        loops ``search()`` — same result shape as the Qdrant wrapper.
        """
        return [
            self.search(
                collection_name=collection_name,
                query_vector=qv,
                limit=limit,
                filters=filters,
            )
            for qv in query_vectors
        ]

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """Return True if Weaviate is reachable."""
        try:
            self._ensure_connected()
            return bool(self._client.is_ready())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_filter(filter_dict: Dict[str, Any]):
        """
        Convert a flat filter dict into a Weaviate Filter.

        - ``{"paper_id": "xxx"}``       -> exact match
        - ``{"paper_id": ["a", "b"]}``  -> match any
        """
        conditions = []
        for key, value in filter_dict.items():
            if isinstance(value, list):
                conditions.append(Filter.by_property(key).contains_any(value))
            else:
                conditions.append(Filter.by_property(key).equal(value))
        if len(conditions) == 1:
            return conditions[0]
        return Filter.all_of(conditions)
