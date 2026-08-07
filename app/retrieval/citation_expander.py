"""
Citation Expansion Engine (Phase 14)

Expands retrieval across citation links: starting from a paper, walks
forward (papers it cites), backward (papers that cite it), or both, up to
a depth limit and capped at a maximum number of discovered papers. Used by
the hybrid retriever (Phase 13) for EVOLUTION-style queries ("how did X
evolve from Y") where the answer needs the citation chain, not just one
paper's own facts.
"""

from typing import Any, Dict, List, Tuple

import structlog

from app.storage.neo4j_client import Neo4jClient

logger = structlog.get_logger()

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_PAPERS = 50
_HARD_MAX_DEPTH = 5


class CitationExpander:
    """Breadth-first traversal of the citation graph, forward and/or backward."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def expand(
        self,
        paper_id: str,
        direction: str = "both",
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_papers: int = DEFAULT_MAX_PAPERS,
    ) -> List[Dict[str, Any]]:
        """
        Breadth-first citation expansion from *paper_id*.

        Parameters
        ----------
        direction : ``"forward"`` (papers cited by this one) |
            ``"backward"`` (papers citing this one) | ``"both"``
        max_depth : traversal hops, clamped to [1, 5] to bound query cost
        max_papers : stop once this many papers have been discovered

        Returns
        -------
        List of dicts, ordered shallowest-first (BFS order), excluding the
        starting paper itself:
        ``{"paper_id", "title", "depth", "path", "direction"}`` where
        ``path`` is the list of paper_ids from the start paper to this one
        (inclusive of both ends).
        """
        max_depth = max(1, min(max_depth, _HARD_MAX_DEPTH))
        max_papers = max(1, max_papers)

        visited = {paper_id}
        frontier: List[Tuple[str, List[str]]] = [(paper_id, [paper_id])]
        results: List[Dict[str, Any]] = []

        for depth in range(1, max_depth + 1):
            if not frontier or len(results) >= max_papers:
                break

            next_frontier: List[Tuple[str, List[str]]] = []
            for current_id, path in frontier:
                for neighbor_id, title, edge_dir in self._neighbors(current_id, direction):
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)

                    new_path = path + [neighbor_id]
                    results.append({
                        "paper_id": neighbor_id,
                        "title": title,
                        "depth": depth,
                        "path": new_path,
                        "direction": edge_dir,
                    })
                    next_frontier.append((neighbor_id, new_path))

                    if len(results) >= max_papers:
                        break
                if len(results) >= max_papers:
                    break

            frontier = next_frontier

        logger.info(
            "citation_expansion",
            paper_id=paper_id,
            direction=direction,
            max_depth=max_depth,
            found=len(results),
        )
        return results[:max_papers]

    def expand_paper_ids(self, paper_id: str, **kwargs: Any) -> List[str]:
        """Convenience wrapper returning just the discovered paper_ids."""
        return [r["paper_id"] for r in self.expand(paper_id, **kwargs)]

    def _neighbors(
        self, paper_id: str, direction: str
    ) -> List[Tuple[str, Any, str]]:
        """Yield (neighbor_paper_id, title, direction_label) for one hop."""
        neighbors: List[Tuple[str, Any, str]] = []

        if direction in ("forward", "both"):
            cypher = (
                "MATCH (p:Paper {paper_id: $pid})-[:CITES]->(cited:Paper) "
                "RETURN cited.paper_id AS pid, cited.title AS title"
            )
            for row in self._client.query(cypher, {"pid": paper_id}):
                neighbors.append((row["pid"], row.get("title"), "forward"))

        if direction in ("backward", "both"):
            cypher = (
                "MATCH (citer:Paper)-[:CITES]->(p:Paper {paper_id: $pid}) "
                "RETURN citer.paper_id AS pid, citer.title AS title"
            )
            for row in self._client.query(cypher, {"pid": paper_id}):
                neighbors.append((row["pid"], row.get("title"), "backward"))

        return neighbors
