"""
Neo4j Client Module (Phase 8.1)

Provides a robust Neo4j driver wrapper with:
- Connection management (lazy init, context manager support)
- Schema initialization (unique constraints, indexes)
- MERGE-based writes for concurrency safety
- Query execution with error handling

Risk Mitigations Addressed:
- Missing Neo4j indexes/constraints: init_schema() creates all needed
  UNIQUE constraints and indexes on first connection.
- Concurrency/race conditions: All write operations use MERGE (not CREATE),
  so concurrent paper ingestions cannot produce duplicate nodes.
- Duplicate prevention: Unique constraints on key properties per node label
  guarantee idempotency even under retries.
"""

from typing import Any, Dict, List, Optional
from contextlib import contextmanager

import structlog

from neo4j import GraphDatabase, Driver

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Node-type  ->  primary-key property used for MERGE deduplication
# Every label in the ontology must have an entry here.
# ---------------------------------------------------------------------------
NODE_KEY_MAP: Dict[str, str] = {
    "Paper": "paper_id",
    "Author": "name",
    "Institution": "name",
    "Method": "name",
    "Dataset": "name",
    "Task": "name",
    "Metric": "name",
    "Claim": "claim_id",
    "Experiment": "experiment_id",
    "Section": "section_id",
}


class Neo4jClient:
    """Neo4j database client with schema-aware operations."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ):
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Optional[Driver] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialize the Neo4j driver and verify connectivity."""
        if self._driver is None:
            logger.info("neo4j_connecting", uri=self._uri)
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            self._driver.verify_connectivity()
            logger.info("neo4j_connected")

    def close(self) -> None:
        """Close the Neo4j driver (call on shutdown)."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None
            logger.info("neo4j_closed")

    @contextmanager
    def session(self):
        """Yield a managed Neo4j session."""
        self._ensure_connected()
        with self._driver.session(database=self._database) as sess:
            yield sess

    def _ensure_connected(self) -> None:
        if self._driver is None:
            self.connect()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """
        Create UNIQUE constraints and indexes for every node type.

        This is idempotent  --  IF NOT EXISTS guards prevent errors on
        repeated calls (e.g. worker restarts).
        """
        self._ensure_connected()

        # --- Unique constraints (also serve as existence indexes) ---
        constraints = [
            ("Paper", "paper_id"),
            ("Author", "name"),
            ("Institution", "name"),
            ("Method", "name"),
            ("Dataset", "name"),
            ("Task", "name"),
            ("Metric", "name"),
            ("Claim", "claim_id"),
            ("Experiment", "experiment_id"),
            ("Section", "section_id"),
        ]

        with self.session() as sess:
            for label, prop in constraints:
                try:
                    sess.run(
                        f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                        f"REQUIRE n.{prop} IS UNIQUE"
                    )
                    logger.debug("constraint_ensured", label=label, prop=prop)
                except Exception as exc:
                    # Neo4j Community edition may reject some constraint ops;
                    # log but don't crash.
                    logger.warning(
                        "constraint_failed", label=label, prop=prop, error=str(exc)
                    )

            # --- Additional range indexes for common query patterns ---
            extra_indexes = [
                ("Paper", "title"),
                ("Paper", "year"),
                ("Paper", "doi"),
                ("Paper", "arxiv_id"),
            ]
            for label, prop in extra_indexes:
                try:
                    sess.run(
                        f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
                    )
                except Exception as exc:
                    logger.warning(
                        "index_failed", label=label, prop=prop, error=str(exc)
                    )

        logger.info("neo4j_schema_initialized")

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def merge_node(self, label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """
        MERGE a node: create if missing, update properties if exists.

        The primary-key property (from ``NODE_KEY_MAP``) is used as the
        MERGE anchor.  All other properties are SET on both CREATE and
        MATCH so re-ingestion overwrites stale data.
        """
        self._ensure_connected()

        key_prop = NODE_KEY_MAP.get(label)
        if not key_prop or key_prop not in properties:
            raise ValueError(
                f"Cannot MERGE {label}: missing key property '{key_prop}'. "
                f"Provided keys: {list(properties.keys())}"
            )

        key_value = properties[key_prop]
        other_props = {k: v for k, v in properties.items() if k != key_prop}

        # Build dynamic SET clause: n.prop0 = $p0, n.prop1 = $p1, ...
        set_parts: list[str] = []
        params: Dict[str, Any] = {"key_value": key_value}
        for idx, (k, v) in enumerate(other_props.items()):
            pname = f"prop_{idx}"
            set_parts.append(f"n.{k} = ${pname}")
            params[pname] = v

        set_clause = ", ".join(set_parts) if set_parts else ""

        if set_clause:
            cypher = (
                f"MERGE (n:{label} {{{key_prop}: $key_value}}) "
                f"ON CREATE SET {set_clause} "
                f"ON MATCH SET {set_clause} "
                f"RETURN n"
            )
        else:
            cypher = (
                f"MERGE (n:{label} {{{key_prop}: $key_value}}) RETURN n"
            )

        with self.session() as sess:
            record = sess.run(cypher, params).single()
            if record:
                return dict(record["n"])
        return {}

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def merge_edge(
        self,
        source_label: str,
        source_key_prop: str,
        source_key_value: Any,
        target_label: str,
        target_key_prop: str,
        target_key_value: Any,
        edge_type: str,
        edge_properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        MERGE a directed relationship between two existing nodes.

        Both nodes MUST already exist (created via ``merge_node``).
        The relationship itself is merged so duplicate edges are prevented.
        """
        self._ensure_connected()
        edge_properties = edge_properties or {}

        set_parts: list[str] = []
        params: Dict[str, Any] = {
            "source_key": source_key_value,
            "target_key": target_key_value,
        }
        for idx, (k, v) in enumerate(edge_properties.items()):
            pname = f"ep_{idx}"
            set_parts.append(f"r.{k} = ${pname}")
            params[pname] = v

        set_clause = ", ".join(set_parts) if set_parts else ""

        if set_clause:
            cypher = (
                f"MATCH (s:{source_label} {{{source_key_prop}: $source_key}}) "
                f"MATCH (t:{target_label} {{{target_key_prop}: $target_key}}) "
                f"MERGE (s)-[r:{edge_type}]->(t) "
                f"ON CREATE SET {set_clause} "
                f"ON MATCH SET {set_clause}"
            )
        else:
            cypher = (
                f"MATCH (s:{source_label} {{{source_key_prop}: $source_key}}) "
                f"MATCH (t:{target_label} {{{target_key_prop}: $target_key}}) "
                f"MERGE (s)-[r:{edge_type}]->(t)"
            )

        with self.session() as sess:
            sess.run(cypher, params)

    # ------------------------------------------------------------------
    # Raw query
    # ------------------------------------------------------------------

    def query(self, cypher: str, params: Optional[Dict] = None) -> List[Dict]:
        """Execute arbitrary Cypher and return list of dicts."""
        self._ensure_connected()
        params = params or {}
        with self.session() as sess:
            result = sess.run(cypher, params)
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    def get_paper_graph(self, paper_id: str) -> List[Dict]:
        """Return every node and edge reachable from a Paper node."""
        cypher = (
            "MATCH (p:Paper {paper_id: $paper_id}) "
            "OPTIONAL MATCH (p)-[r]-(n) "
            "RETURN p, r, n"
        )
        return self.query(cypher, {"paper_id": paper_id})

    def delete_paper_graph(self, paper_id: str) -> int:
        """DETACH DELETE a Paper and count removed nodes."""
        results = self.query(
            "MATCH (p:Paper {paper_id: $pid}) "
            "OPTIONAL MATCH (p)-[r]-(n) "
            "DETACH DELETE p "
            "RETURN count(p) AS deleted",
            {"pid": paper_id},
        )
        return results[0]["deleted"] if results else 0

    def health_check(self) -> bool:
        """Return True if Neo4j is reachable and responding."""
        try:
            self._ensure_connected()
            rows = self.query("RETURN 1 AS health")
            return bool(rows) and rows[0].get("health") == 1
        except Exception:
            return False