"""
Graph Repository Module (Phase 8.2)

High-level operations for storing and querying paper knowledge graphs
in Neo4j.  Sits between ``PaperGraphBuilder`` (produces ``Node``/``Edge``
objects) and ``Neo4jClient`` (raw Cypher).

Key capabilities:
- Idempotent paper-graph storage (safe re-ingestion)
- Citation-graph traversal (forward / backward / bidirectional)
- Entity-centric queries (papers using a method, methods on a dataset, etc.)
- Citation-stub resolution (re-wire stubs when the real paper arrives)
"""

from typing import Any, Dict, List, Optional

import structlog

from app.graph.ontology import Node, Edge
from app.storage.neo4j_client import Neo4jClient

logger = structlog.get_logger()


class GraphRepository:
    """High-level repository for paper knowledge graph operations."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def store_paper_graph(
        self,
        paper_id: str,
        nodes: List[Node],
        edges: List[Edge],
    ) -> Dict[str, int]:
        """
        Store a complete paper graph in Neo4j.

        All writes use MERGE, so this is safe to call on re-ingestion.
        Returns ``{"nodes_stored": int, "edges_stored": int}``.
        """
        nodes_stored = 0
        edges_stored = 0

        # --- nodes ---
        for node in nodes:
            try:
                # node_id is the MERGE anchor (see Neo4jClient.merge_node);
                # name/paper_id are stored as ordinary, indexed properties
                # for retrieval lookups, not identity.
                props: Dict[str, Any] = {
                    "node_id": node.node_id,
                    "name": node.name,
                    "paper_id": node.paper_id,
                }
                props.update(node.properties)

                self._client.merge_node(node.node_type, props)
                nodes_stored += 1
            except Exception as exc:
                logger.error("node_store_failed", node_id=node.node_id, error=str(exc))

        # --- edges (must come after nodes so MATCH can find them) ---
        for edge in edges:
            try:
                edge_props: Dict[str, Any] = {"confidence": edge.confidence}
                if edge.evidence:
                    edge_props["evidence"] = edge.evidence
                edge_props.update(edge.properties)

                self._client.merge_edge(
                    source_label=edge.source_type,
                    source_key_prop="node_id",
                    source_key_value=edge.source_id,
                    target_label=edge.target_type,
                    target_key_prop="node_id",
                    target_key_value=edge.target_id,
                    edge_type=edge.edge_type,
                    edge_properties=edge_props,
                )
                edges_stored += 1
            except Exception as exc:
                logger.error(
                    "edge_store_failed",
                    src=edge.source_id,
                    tgt=edge.target_id,
                    rel=edge.edge_type,
                    error=str(exc),
                )

        logger.info(
            "paper_graph_stored",
            paper_id=paper_id,
            nodes=nodes_stored,
            edges=edges_stored,
        )
        return {"nodes_stored": nodes_stored, "edges_stored": edges_stored}

    # ------------------------------------------------------------------
    # Read operations  -- citation graph
    # ------------------------------------------------------------------

    def get_citation_graph(self) -> Dict[str, Any]:
        """
        Return every paper (real or stub) and every CITES edge between them --
        the whole cross-paper citation network, for the global graph explorer.

        Returns ``{"papers": [...], "edges": [...]}``. Each paper has
        ``paper_id``/``title``/``name``/``year``/``is_stub``; each edge has
        ``source``/``target`` paper_ids.
        """
        papers = self._client.query(
            "MATCH (p:Paper) RETURN p.paper_id AS paper_id, p.title AS title, "
            "p.name AS name, p.year AS year, p.is_stub AS is_stub"
        )
        edges = self._client.query(
            "MATCH (a:Paper)-[:CITES]->(b:Paper) "
            "RETURN a.paper_id AS source, b.paper_id AS target"
        )
        return {"papers": papers, "edges": edges}

    def find_papers_citing(self, paper_id: str) -> List[Dict[str, Any]]:
        """Papers that cite the given paper (backward traversal)."""
        cypher = (
            "MATCH (citer:Paper)-[:CITES]->(p:Paper {paper_id: $pid}) "
            "RETURN citer {paper_id: citer.paper_id, title: citer.title, "
            "year: citer.year, is_stub: citer.is_stub} AS paper"
        )
        return self._client.query(cypher, {"pid": paper_id})

    def find_papers_cited_by(self, paper_id: str) -> List[Dict[str, Any]]:
        """Papers cited by the given paper (forward traversal)."""
        cypher = (
            "MATCH (p:Paper {paper_id: $pid})-[:CITES]->(cited:Paper) "
            "RETURN cited {paper_id: cited.paper_id, title: cited.title, "
            "year: cited.year, is_stub: cited.is_stub} AS paper"
        )
        return self._client.query(cypher, {"pid": paper_id})

    def get_citation_neighbors(
        self,
        paper_id: str,
        direction: str = "both",
        depth: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Traverse the citation graph from *paper_id*.

        Parameters
        ----------
        direction : ``"forward"`` | ``"backward"`` | ``"both"``
        depth : traversal hops (1 = direct neighbours only)
        """
        depth = max(1, min(depth, 5))  # clamp to prevent runaway queries

        if direction == "forward":
            pattern = f"-[:CITES*1..{depth}]->(related:Paper)"
        elif direction == "backward":
            pattern = f"<-[:CITES*1..{depth}]-(related:Paper)"
        else:
            pattern = f"-[:CITES*1..{depth}]-(related:Paper)"

        cypher = (
            f"MATCH (p:Paper {{paper_id: $pid}}){pattern} "
            "WHERE related.paper_id <> $pid "
            "RETURN DISTINCT related {paper_id: related.paper_id, "
            "title: related.title, year: related.year, "
            "is_stub: related.is_stub} AS paper"
        )
        return self._client.query(cypher, {"pid": paper_id})

    # ------------------------------------------------------------------
    # Read operations  -- entity queries
    # ------------------------------------------------------------------

    def find_entities_by_type(
        self, entity_type: str, paper_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List entities of a given type, optionally filtered by paper."""
        if paper_id:
            cypher = (
                f"MATCH (e:{entity_type} {{paper_id: $pid}}) "
                "RETURN e {name: e.name, paper_id: e.paper_id, "
                "evidence: e.evidence, source_section: e.source_section} AS entity"
            )
            return self._client.query(cypher, {"pid": paper_id})
        cypher = (
            f"MATCH (e:{entity_type}) "
            "RETURN e {name: e.name, paper_id: e.paper_id, "
            "evidence: e.evidence, source_section: e.source_section} AS entity"
        )
        return self._client.query(cypher)

    def find_papers_using_method(self, method_name: str) -> List[Dict[str, Any]]:
        """Papers that mention a method by name."""
        cypher = (
            "MATCH (p:Paper)-[:MENTIONS]->(m:Method {name: $name}) "
            "RETURN DISTINCT p {paper_id: p.paper_id, title: p.title, "
            "year: p.year} AS paper"
        )
        return self._client.query(cypher, {"name": method_name})

    def find_papers_using_dataset(self, dataset_name: str) -> List[Dict[str, Any]]:
        """Papers that use or mention a dataset."""
        cypher = (
            "MATCH (p:Paper)-[:USES_DATASET|MENTIONS]->(d:Dataset {name: $name}) "
            "RETURN DISTINCT p {paper_id: p.paper_id, title: p.title, "
            "year: p.year} AS paper"
        )
        return self._client.query(cypher, {"name": dataset_name})

    def find_methods_improving_upon(self, method_name: str) -> List[Dict[str, Any]]:
        """Methods that IMPROVES_UPON the given method."""
        cypher = (
            "MATCH (improver:Method)-[:IMPROVES_UPON]->(m:Method {name: $name}) "
            "RETURN DISTINCT improver {name: improver.name, "
            "paper_id: improver.paper_id} AS method"
        )
        return self._client.query(cypher, {"name": method_name})

    def find_methods_evaluated_on(self, dataset_name: str) -> List[Dict[str, Any]]:
        """Methods evaluated on a specific dataset."""
        cypher = (
            "MATCH (m:Method)-[:USES_DATASET|EVALUATES_ON]->(d:Dataset {name: $name}) "
            "RETURN DISTINCT m {name: m.name, paper_id: m.paper_id} AS method"
        )
        return self._client.query(cypher, {"name": dataset_name})

    def get_entity_relations(
        self, entity_name: str, entity_type: str
    ) -> List[Dict[str, Any]]:
        """All relationships involving a specific entity."""
        cypher = (
            f"MATCH (e:{entity_type} {{name: $name}}) "
            "MATCH (e)-[r]-(other) "
            "RETURN type(r) AS relation_type, "
            "startNode(r).name AS source_name, "
            "endNode(r).name AS target_name, "
            "r.evidence AS evidence"
        )
        return self._client.query(cypher, {"name": entity_name})

    # ------------------------------------------------------------------
    # Citation stub resolution
    # ------------------------------------------------------------------

    def find_real_paper_id(
        self, doi: Optional[str] = None, arxiv_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Look up an already-ingested (non-stub) paper by its self-identity.

        Used for the "real paper ingested first, citing paper arrives
        later" ordering: when paper Y is ingested and cites X by DOI/arXiv
        ID, if X already exists as a real node this finds its paper_id so
        the stub ``_make_citation_pair`` would otherwise create for X can
        be resolved to it immediately instead.
        """
        if doi:
            rows = self._client.query(
                "MATCH (p:Paper {doi: $doi, is_stub: False}) "
                "RETURN p.paper_id AS pid LIMIT 1",
                {"doi": doi},
            )
        elif arxiv_id:
            rows = self._client.query(
                "MATCH (p:Paper {arxiv_id: $arxiv_id, is_stub: False}) "
                "RETURN p.paper_id AS pid LIMIT 1",
                {"arxiv_id": arxiv_id},
            )
        else:
            return None
        return rows[0]["pid"] if rows else None

    def resolve_citation_stub(
        self, stub_paper_id: str, real_paper_id: str
    ) -> None:
        """
        Re-wire CITES edges from a stub Paper node to the real Paper node,
        then delete the stub.

        Called automatically by ``PaperIngestionPipeline`` after storing a
        new paper, in case previously-ingested papers cited it via a stub.

        Guards against self-citation: a paper should never end up CITES-ing
        itself, which is what a wrong ``real_paper_id`` (e.g. accidentally
        passing the citing paper's own id instead of the paper it cites)
        would otherwise produce, deleting the stub in the process. This is
        a defense-in-depth check -- the real fix is calling this with the
        correct arguments in the first place (see
        ``PaperIngestionPipeline._try_resolve_stubs``).
        """
        cypher = """
        // Find the stub (must be marked is_stub)
        MATCH (stub:Paper {paper_id: $stub_pid, is_stub: True})
        // Find the real paper
        MATCH (real:Paper {paper_id: $real_pid})
        WHERE stub <> real
        // For every OTHER paper that CITES the stub, create a CITES to the
        // real paper -- excluding the real paper itself, so this can never
        // produce a self-loop.
        OPTIONAL MATCH (citer:Paper)-[r:CITES]->(stub)
        WHERE citer <> real
        FOREACH (_ IN CASE WHEN citer IS NOT NULL THEN [1] ELSE [] END |
            MERGE (citer)-[:CITES]->(real)
        )
        DETACH DELETE stub
        """
        self._client.query(cypher, {"stub_pid": stub_paper_id, "real_pid": real_paper_id})
        logger.info("citation_stub_resolved", stub=stub_paper_id, real=real_paper_id)