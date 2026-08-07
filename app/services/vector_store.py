"""
Legacy vector-store service for the ``/chat`` path.

Rewritten against Weaviate (ADR: Weaviate replaces Qdrant) while keeping
the original public surface — ``get_client``, ``init_collection``,
``upsert_vectors``, ``search_vectors`` — and the original result shape
(hits exposing a ``.payload`` attribute), so ``app/api/routes.py`` and
its tests are untouched.

Internally this now delegates to ``WeaviateClientWrapper`` instead of
driving the raw client, so the legacy path and the GraphRAG path share
one connection/config story.
"""

import uuid
from types import SimpleNamespace

import structlog

from app.core.config import settings
from app.storage.weaviate_client import VectorPoint, WeaviateClientWrapper

logger = structlog.get_logger()

# Legacy default: all-MiniLM-L6-v2 embeddings.
_LEGACY_DIM = 384

_client = None


def get_client() -> WeaviateClientWrapper:
    global _client
    if _client is None:
        _client = WeaviateClientWrapper(
            url=settings.WEAVIATE_URL,
            api_key=settings.WEAVIATE_API_KEY,
            grpc_port=settings.WEAVIATE_GRPC_PORT,
            batch_size=settings.WEAVIATE_BATCH_SIZE,
        )
    return _client


def init_collection():
    """Ensure the shared collection exists (legacy 384-dim default)."""
    get_client().ensure_collection(
        collection_name=settings.WEAVIATE_COLLECTION_NAME,
        vector_dim=_LEGACY_DIM,
        distance=settings.WEAVIATE_DISTANCE_METRIC,
    )


def upsert_vectors(collection_name: str, embeddings_data: list):
    """
    embeddings_data: list of dicts ``{"vector": ..., "payload": ...}``
    """
    client = get_client()
    init_collection()

    points = [
        VectorPoint(
            id=str(uuid.uuid4()),  # legacy path: random point IDs
            vector=item["vector"],
            payload=item["payload"],
        )
        for item in embeddings_data
    ]

    if points:
        stored = client.upsert(collection_name, points)
        logger.info(f"Upserted {stored} points to {collection_name}")


def search_vectors(query_vector: list, top_k: int = 5, doc_id: str = None):
    """
    Top-k similarity search. Returns hit objects with ``.id``,
    ``.score`` and ``.payload`` attributes (legacy shape used by /chat).

    Chunks are stored keyed by "paper_id" (see VectorRepository), not
    "doc_id" -- filtering on the wrong key silently matched nothing.
    """
    client = get_client()

    filters = {"paper_id": doc_id} if doc_id else None
    results = client.search(
        collection_name=settings.WEAVIATE_COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k,
        filters=filters,
    )

    return [
        SimpleNamespace(id=hit["id"], score=hit["score"], payload=hit["payload"])
        for hit in results
    ]
