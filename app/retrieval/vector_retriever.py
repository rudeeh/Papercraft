"""
Vector Retriever Module (Phase 12)

Thin retrieval-layer wrapper around ``VectorRepository`` (Phase 9) for
semantic search: embeds the query, runs top-k similarity search in Qdrant,
and supports filtering by paper_id(s) and/or node_type. Results are
normalized into a flat shape for the hybrid retriever (Phase 13) and answer
generator (Phase 15).
"""

from typing import Any, Dict, List, Optional, Union

import structlog

from app.storage.vector_repository import VectorRepository

logger = structlog.get_logger()


class VectorRetriever:
    """Semantic (vector) retrieval over paper chunk/entity embeddings."""

    def __init__(self, vector_repo: VectorRepository) -> None:
        self._repo = vector_repo

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        paper_id: Optional[Union[str, List[str]]] = None,
        node_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the top-k chunks most similar to *query*.

        Parameters
        ----------
        query : natural-language query text
        top_k : number of results
        paper_id : restrict to a single paper_id, or a list of paper_ids
            (e.g. a set derived from graph traversal)
        node_type : restrict to a single graph node type (e.g. "Method", "Claim")

        Returns
        -------
        List of dicts: id, score, text, paper_id, section, node_type,
        node_name, source_text, page.
        """
        if not query or not query.strip():
            return []

        filters: Dict[str, Any] = {}
        if paper_id:
            filters["paper_id"] = paper_id
        if node_type:
            filters["node_type"] = node_type

        raw_results = self._repo.similarity_search(
            query=query, top_k=top_k, filters=filters or None,
        )
        results = [self._normalize(hit) for hit in raw_results]

        logger.info(
            "vector_retrieval",
            query=query,
            top_k=top_k,
            filters=filters or None,
            results=len(results),
        )
        return results

    @staticmethod
    def _normalize(hit: Dict[str, Any]) -> Dict[str, Any]:
        payload = hit.get("payload") or {}
        return {
            "id": hit.get("id"),
            "score": hit.get("score"),
            "text": payload.get("text"),
            "paper_id": payload.get("paper_id"),
            "section": payload.get("section"),
            "node_type": payload.get("node_type"),
            "node_name": payload.get("node_name"),
            "source_text": payload.get("source_text"),
            "page": payload.get("page"),
        }
