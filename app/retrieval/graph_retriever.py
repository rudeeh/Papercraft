"""
Graph Retriever Module (Phase 11)

Retrieves research-paper knowledge graph facts via Neo4j traversal, in a
format ready for hybrid retrieval (Phase 13) and answer generation (Phase 15).

Every result is a "graph fact": a subject/relation/object triple, plus the
evidence text backing it (when the underlying node/edge captured evidence)
and the paper_id(s) that ground the fact -- so an answer built from these
facts can always be traced back to a source paper.
"""

from typing import Any, Dict, List, Optional

import structlog

from app.storage.neo4j_client import Neo4jClient

logger = structlog.get_logger()


def _node_ref(name: Optional[str], node_type: str, paper_id: Optional[str] = None) -> Dict[str, Any]:
    return {"name": name, "type": node_type, "paper_id": paper_id}


def _fact(
    subject: Dict[str, Any],
    relation: Optional[str],
    obj: Dict[str, Any],
    evidence: Optional[str] = None,
    source_paper_ids: Optional[List[Optional[str]]] = None,
) -> Dict[str, Any]:
    return {
        "subject": subject,
        "relation": relation,
        "object": obj,
        "evidence": evidence,
        "source_paper_ids": sorted({pid for pid in (source_paper_ids or []) if pid}),
    }


def _first_label(labels: Optional[List[str]]) -> str:
    return labels[0] if labels else "Entity"


class GraphRetriever:
    """Graph-traversal retrieval over the paper knowledge graph in Neo4j."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Citation queries
    # ------------------------------------------------------------------

    def find_citing_papers(self, paper_id: str) -> List[Dict[str, Any]]:
        """Papers that cite ``paper_id`` (backward citation traversal)."""
        cypher = (
            "MATCH (citer:Paper)-[r:CITES]->(p:Paper {paper_id: $pid}) "
            "RETURN citer.paper_id AS citer_id, citer.title AS citer_title, "
            "r.evidence AS evidence, p.title AS cited_title"
        )
        rows = self._client.query(cypher, {"pid": paper_id})
        facts = []
        for row in rows:
            subject = _node_ref(row.get("citer_title") or row.get("citer_id"), "Paper", row.get("citer_id"))
            obj = _node_ref(row.get("cited_title") or paper_id, "Paper", paper_id)
            facts.append(_fact(subject, "CITES", obj, evidence=row.get("evidence"),
                                source_paper_ids=[row.get("citer_id"), paper_id]))
        return facts

    def find_cited_papers(self, paper_id: str) -> List[Dict[str, Any]]:
        """Papers cited by ``paper_id`` (forward citation traversal)."""
        cypher = (
            "MATCH (p:Paper {paper_id: $pid})-[r:CITES]->(cited:Paper) "
            "RETURN cited.paper_id AS cited_id, cited.title AS cited_title, "
            "r.evidence AS evidence"
        )
        rows = self._client.query(cypher, {"pid": paper_id})
        facts = []
        for row in rows:
            subject = _node_ref(paper_id, "Paper", paper_id)
            obj = _node_ref(row.get("cited_title") or row.get("cited_id"), "Paper", row.get("cited_id"))
            facts.append(_fact(subject, "CITES", obj, evidence=row.get("evidence"),
                                source_paper_ids=[paper_id]))
        return facts

    # ------------------------------------------------------------------
    # Dataset / method queries
    # ------------------------------------------------------------------

    def find_papers_using_dataset(self, dataset_name: str) -> List[Dict[str, Any]]:
        """Papers that use or mention a dataset."""
        cypher = (
            "MATCH (p:Paper)-[r:USES_DATASET|MENTIONS]->(d:Dataset {name: $name}) "
            "RETURN DISTINCT p.paper_id AS paper_id, p.title AS title, "
            "r.evidence AS evidence, type(r) AS relation"
        )
        rows = self._client.query(cypher, {"name": dataset_name})
        facts = []
        for row in rows:
            subject = _node_ref(row.get("title") or row.get("paper_id"), "Paper", row.get("paper_id"))
            obj = _node_ref(dataset_name, "Dataset")
            facts.append(_fact(subject, row.get("relation") or "USES_DATASET", obj,
                                evidence=row.get("evidence"),
                                source_paper_ids=[row.get("paper_id")]))
        return facts

    def find_methods_evaluated_on(self, dataset_name: str) -> List[Dict[str, Any]]:
        """Methods evaluated on a specific dataset."""
        cypher = (
            "MATCH (m:Method)-[r:USES_DATASET|EVALUATES_ON]->(d:Dataset {name: $name}) "
            "RETURN DISTINCT m.name AS method_name, m.paper_id AS paper_id, "
            "r.evidence AS evidence, type(r) AS relation"
        )
        rows = self._client.query(cypher, {"name": dataset_name})
        facts = []
        for row in rows:
            subject = _node_ref(row.get("method_name"), "Method", row.get("paper_id"))
            obj = _node_ref(dataset_name, "Dataset")
            facts.append(_fact(subject, row.get("relation") or "EVALUATES_ON", obj,
                                evidence=row.get("evidence"),
                                source_paper_ids=[row.get("paper_id")]))
        return facts

    def find_methods_improving_upon(self, method_name: str) -> List[Dict[str, Any]]:
        """Methods that IMPROVES_UPON the given method."""
        cypher = (
            "MATCH (improver:Method)-[r:IMPROVES_UPON]->(m:Method {name: $name}) "
            "RETURN DISTINCT improver.name AS improver_name, "
            "improver.paper_id AS paper_id, r.evidence AS evidence"
        )
        rows = self._client.query(cypher, {"name": method_name})
        facts = []
        for row in rows:
            subject = _node_ref(row.get("improver_name"), "Method", row.get("paper_id"))
            obj = _node_ref(method_name, "Method")
            facts.append(_fact(subject, "IMPROVES_UPON", obj,
                                evidence=row.get("evidence"),
                                source_paper_ids=[row.get("paper_id")]))
        return facts

    # ------------------------------------------------------------------
    # Entity queries
    # ------------------------------------------------------------------

    def find_entities_for_paper(self, paper_id: str) -> List[Dict[str, Any]]:
        """All entities (Method/Dataset/Task/Metric/Claim/Experiment) mentioned by a paper."""
        cypher = (
            "MATCH (p:Paper {paper_id: $pid})-[r:MENTIONS]->(e) "
            "RETURN labels(e) AS labels, e.name AS name, "
            "coalesce(r.evidence, e.evidence) AS evidence"
        )
        rows = self._client.query(cypher, {"pid": paper_id})
        facts = []
        for row in rows:
            subject = _node_ref(paper_id, "Paper", paper_id)
            obj = _node_ref(row.get("name"), _first_label(row.get("labels")), paper_id)
            facts.append(_fact(subject, "MENTIONS", obj, evidence=row.get("evidence"),
                                source_paper_ids=[paper_id]))
        return facts

    def get_entity_relations(self, entity_name: str, entity_type: str) -> List[Dict[str, Any]]:
        """All relationships involving a specific entity, in either direction."""
        cypher = (
            f"MATCH (e:{entity_type} {{name: $name}}) "
            "MATCH (e)-[r]-(other) "
            "RETURN type(r) AS relation, "
            "startNode(r).name AS source_name, labels(startNode(r)) AS source_labels, "
            "endNode(r).name AS target_name, labels(endNode(r)) AS target_labels, "
            "r.evidence AS evidence, "
            "coalesce(startNode(r).paper_id, endNode(r).paper_id) AS paper_id"
        )
        rows = self._client.query(cypher, {"name": entity_name})
        facts = []
        for row in rows:
            subject = _node_ref(row.get("source_name"), _first_label(row.get("source_labels")))
            obj = _node_ref(row.get("target_name"), _first_label(row.get("target_labels")))
            facts.append(_fact(subject, row.get("relation"), obj, evidence=row.get("evidence"),
                                source_paper_ids=[row.get("paper_id")]))
        return facts
