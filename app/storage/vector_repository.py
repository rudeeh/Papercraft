"""
Vector Repository Module (Phase 9.3)

High-level operations for storing and querying paper chunk embeddings
in Qdrant.  Sits between ``EmbeddingService`` (produces vectors) and
``QdrantClientWrapper`` (raw Qdrant operations).

Key capabilities
----------------
- ``store_paper_chunks()``: embed text chunks and upsert into Qdrant with
  rich metadata (paper_id, section, page, chunk_index).
- ``similarity_search()``: embed a query and find top-k similar chunks.
- ``hybrid_search()``: combine vector similarity with graph context from
  Neo4j (e.g. only search within papers that cite a given paper).
- ``delete_paper_vectors()``: remove all vectors for a paper (for re-ingestion).
- ``paper_search()``: find papers whose chunks are most similar to a query,
  with score aggregation.

Risk Mitigations Addressed
--------------------------
- Re-ingestion safety: ``store_paper_chunks`` calls
  ``delete_paper_vectors`` first, so re-ingesting a paper always
  produces a clean state with no stale vectors.
- Embedding-Qdrant dim mismatch: ``ensure_collection`` is called on
  every ``store_paper_chunks`` invocation, so model swaps are handled
  automatically.
- Missing chunks handled gracefully: empty input lists produce no
  errors and return zero-count results.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

from app.embeddings.embedder import EmbeddingService
from app.storage.qdrant_client import QdrantClientWrapper
from qdrant_client.http import models as qmodels

logger = structlog.get_logger()


class VectorRepository:
    """
    High-level repository for paper vector storage and retrieval.

    Combines embedding generation with Qdrant operations so callers
    don't need to manage the embedding step separately.
    """

    def __init__(
        self,
        qdrant: QdrantClientWrapper,
        embedder: EmbeddingService,
        collection_name: str = "documents",
    ) -> None:
        self._qdrant = qdrant
        self._embedder = embedder
        self._collection = collection_name

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @property
    def embedder(self) -> EmbeddingService:
        return self._embedder

    def store_paper_chunks(
        self,
        paper_id: str,
        chunks: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """
        Embed text chunks and store them in Qdrant.

        Parameters
        ----------
        paper_id : unique paper identifier
        chunks : list of dicts, each with at least:
            - ``text`` (str): the chunk text to embed
            - ``section`` (str, optional): section name (e.g. "Abstract")
            - ``page`` (int, optional): page number
            - ``chunk_index`` (int, optional): chunk order within paper
            - ``node_type`` / ``node_name`` (str, optional): links this chunk
              back to the paper-graph node it was derived from
            - ``source_text`` (str, optional): the original evidence text,
              when ``text`` has been reformatted for embedding

        Returns
        -------
        ``{"chunks_stored": int, "chunks_deleted": int}``

        The collection is created (or validated) automatically based on
        the current embedder dimension.
        """
        if not chunks:
            logger.info("no_chunks_to_store", paper_id=paper_id)
            return {"chunks_stored": 0, "chunks_deleted": 0}

        texts = [c["text"] for c in chunks]
        logger.info("embedding_chunks", paper_id=paper_id, count=len(texts))
        vectors = self._embedder.embed(texts)

        return self.store_embedded_chunks(paper_id, chunks, vectors)

    def store_embedded_chunks(
        self,
        paper_id: str,
        chunks: List[Dict[str, Any]],
        vectors: List[List[float]],
    ) -> Dict[str, int]:
        """
        Store chunks whose vectors have already been computed (skips
        re-embedding). Useful when a caller wants to track the embedding
        step separately from the storage step.
        """
        if not chunks:
            logger.info("no_chunks_to_store", paper_id=paper_id)
            return {"chunks_stored": 0, "chunks_deleted": 0}

        # Ensure collection exists with correct dimension
        self._qdrant.ensure_collection(
            collection_name=self._collection,
            vector_dim=self._embedder.embed_dim,
        )

        # Delete existing vectors for this paper (re-ingestion safety)
        try:
            self._qdrant.delete_points(self._collection, paper_id)
            deleted_estimate = -1  # Qdrant doesn't return count
        except Exception as exc:
            logger.warning(
                "vector_delete_before_upsert_failed",
                paper_id=paper_id,
                error=str(exc),
            )
            deleted_estimate = 0

        point_ids = [
            f"{paper_id}__chunk_{i:05d}" for i in range(len(chunks))
        ]

        # Build Qdrant PointStructs
        points: List[qmodels.PointStruct] = []
        for pid, vec, chunk in zip(point_ids, vectors, chunks):
            payload: Dict[str, Any] = {
                "paper_id": paper_id,
                "text": chunk["text"],
            }
            # Attach optional metadata fields
            for meta_key in (
                "section", "page", "chunk_index",
                "node_type", "node_name", "source_text",
            ):
                if meta_key in chunk:
                    payload[meta_key] = chunk[meta_key]

            points.append(
                qmodels.PointStruct(
                    id=pid,
                    vector=vec,
                    payload=payload,
                )
            )

        # Upsert in batches (handled by QdrantClientWrapper)
        stored = self._qdrant.upsert(self._collection, points)

        logger.info(
            "paper_chunks_stored",
            paper_id=paper_id,
            chunks_stored=stored,
        )
        return {"chunks_stored": stored, "chunks_deleted": deleted_estimate}

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def similarity_search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Embed a query string and find the top-k most similar chunks.

        Parameters
        ----------
        query : natural-language query text
        top_k : number of results
        filters : optional payload filter, e.g.
            ``{"paper_id": "xxx"}`` or ``{"section": "Abstract"}``

        Returns list of dicts with: id, score, payload (containing
        paper_id, text, section, page, chunk_index).
        """
        query_vector = self._embedder.embed_single(query)
        if not query_vector:
            return []

        results = self._qdrant.search(
            collection_name=self._collection,
            query_vector=query_vector,
            limit=top_k,
            filters=filters,
        )
        return results

    def hybrid_search(
        self,
        query: str,
        graph_paper_ids: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Vector search restricted to a graph-derived set of papers.

        This is the bridge between Neo4j (graph) and Qdrant (vectors).
        For example, the graph identifies "papers that cite paper X",
        and then we search *only* within those papers' chunks.

        Parameters
        ----------
        query : natural-language query text
        graph_paper_ids : list of paper_ids from Neo4j traversal.
            If empty or None, falls back to unrestricted search.
        top_k : number of results

        Returns the same format as ``similarity_search()``.
        """
        filters = None
        if graph_paper_ids:
            filters = {"paper_id": graph_paper_ids}
            logger.info(
                "hybrid_search_with_graph_filter",
                paper_count=len(graph_paper_ids),
            )

        return self.similarity_search(
            query=query, top_k=top_k, filters=filters,
        )

    def paper_search(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Find the most relevant *papers* for a query (not chunks).

        Retrieves more chunks than ``top_k``, then aggregates scores
        per paper_id and returns the top papers by best chunk score.

        Returns list of dicts: paper_id, best_score, chunk_count,
        top_sections (list of section names from top chunks).
        """
        # Fetch more raw chunks for aggregation
        raw_results = self.similarity_search(query, top_k=top_k * 3)

        if not raw_results:
            return []

        # Aggregate by paper_id
        paper_scores: Dict[str, Dict[str, Any]] = {}
        for hit in raw_results:
            pid = hit["payload"].get("paper_id", "unknown")
            if pid not in paper_scores:
                paper_scores[pid] = {
                    "paper_id": pid,
                    "best_score": hit["score"],
                    "chunk_count": 0,
                    "sections": set(),
                    "sample_text": hit["payload"].get("text", ""),
                }
            paper_scores[pid]["best_score"] = max(
                paper_scores[pid]["best_score"], hit["score"]
            )
            paper_scores[pid]["chunk_count"] += 1
            section = hit["payload"].get("section")
            if section:
                paper_scores[pid]["sections"].add(section)

        # Sort by best score and return top_k
        sorted_papers = sorted(
            paper_scores.values(), key=lambda x: x["best_score"], reverse=True,
        )[:top_k]

        # Convert sets to lists for JSON serialization
        for p in sorted_papers:
            p["sections"] = sorted(p["sections"])

        return sorted_papers

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    def delete_paper_vectors(self, paper_id: str) -> None:
        """Remove all vectors for a paper (useful before re-ingestion)."""
        try:
            self._qdrant.delete_points(self._collection, paper_id)
            logger.info("paper_vectors_deleted", paper_id=paper_id)
        except Exception as exc:
            logger.error(
                "paper_vector_delete_failed",
                paper_id=paper_id,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_collection_stats(self) -> Optional[Dict[str, Any]]:
        """Return collection metadata (point count, dimension, etc.)."""
        return self._qdrant.collection_info(self._collection)