"""
Paper Graph Builder Module (Phase 7)

Assembles extracted entities, relations, and citations into a structured
per-paper knowledge graph (nodes + edges) that is ready for Neo4j storage.

Risk Mitigations Addressed:
- Citation resolution gap: For each extracted citation a *stub* Paper node
  is created with stable IDs derived from DOI / arXiv ID / title hash.
  When the real paper is later uploaded, ``GraphRepository.resolve_citation_stub()``
  re-wires CITES edges from the stub to the real node and deletes the stub.
- Deduplication: Entity nodes are deduplicated by (type, normalised_name).
  Edge deduplication uses (source_id, edge_type, target_id) triplets.
"""

import hashlib
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.graph.ontology import Node, Edge, OntologyValidator

logger = structlog.get_logger()


def _stable_hash(text: str) -> str:
    """Short, deterministic hash used in node IDs."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


class PaperGraphBuilder:
    """
    Builds a knowledge graph from parsed paper data.

    Consumes outputs of:
    - ``PaperParser``  (title, abstract, sections)
    - ``CitationNormalizer`` (normalised references)
    - ``EntityExtractor`` (entities)
    - ``RelationExtractor`` (relations)

    Produces ``{"nodes": [Node, ...], "edges": [Edge, ...]}`` that conforms
    to the ontology defined in ``app/graph/ontology.py``.
    """

    def __init__(self) -> None:
        self._validator = OntologyValidator()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        paper_id: str,
        title: Optional[str] = None,
        abstract: Optional[str] = None,
        authors: Optional[List[str]] = None,
        year: Optional[int] = None,
        sections: Optional[List[Dict[str, str]]] = None,
        entities: Optional[List[Dict[str, str]]] = None,
        relations: Optional[List[Dict[str, str]]] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List]:
        """
        Build a paper knowledge graph.

        Parameters
        ----------
        paper_id : str
            Unique identifier for the paper (e.g. SHA-256 hash of the PDF).
        title, abstract : optional str
        authors : optional list[str]
        year : optional int
        sections : optional list[{"heading": str, "text": str}]
        entities : optional list[{"name": str, "type": str, "source_section": str, "evidence": str}]
        relations : optional list[{"source": str, "source_type": str, "relation": str,
                                    "target": str, "target_type": str, "evidence": str}]
        citations : optional list[normalised reference dicts from CitationNormalizer]

        Returns
        -------
        {"nodes": list[Node], "edges": list[Edge]}
        """
        authors = authors or []
        sections = sections or []
        entities = entities or []
        relations = relations or []
        citations = citations or []

        nodes: List[Node] = []
        edges: List[Edge] = []

        # 1. Paper node
        paper_node = self._make_paper_node(paper_id, title, abstract, authors, year)
        nodes.append(paper_node)

        # 2. Author nodes  +  WRITTEN_BY edges
        for author_name in authors:
            author_node = self._make_author_node(author_name)
            nodes.append(author_node)
            edge = self._safe_edge(
                paper_node.node_id, "Paper",
                "WRITTEN_BY",
                author_node.node_id, "Author",
            )
            if edge:
                edges.append(edge)

        # 3. Section nodes  +  HAS_SECTION edges
        for idx, sec in enumerate(sections):
            sec_node = self._make_section_node(paper_id, sec["heading"], sec.get("text", ""), idx)
            nodes.append(sec_node)
            edge = self._safe_edge(
                paper_node.node_id, "Paper",
                "HAS_SECTION",
                sec_node.node_id, "Section",
            )
            if edge:
                edges.append(edge)

        # 4. Entity nodes  +  Paper MENTIONS entity edges
        entity_id_map: Dict[Tuple[str, str], str] = {}  # (type, name_lower) -> node_id
        for ent in entities:
            ent_node = self._make_entity_node(paper_id, ent)
            if ent_node is None:
                continue
            key = (ent["type"], ent["name"].lower())
            if key in entity_id_map:
                continue  # deduplicate within this paper
            entity_id_map[key] = ent_node.node_id
            nodes.append(ent_node)

            mention_edge = self._safe_edge(
                paper_node.node_id, "Paper",
                "MENTIONS",
                ent_node.node_id, ent["type"],
            )
            if mention_edge:
                edges.append(mention_edge)

        # 5. Relation edges between entities
        for rel in relations:
            rel_edge = self._make_relation_edge(rel, entity_id_map)
            if rel_edge:
                edges.append(rel_edge)

        # 6. Citation stub Paper nodes  +  CITES edges
        existing_ids = {n.node_id for n in nodes}
        for cit in citations:
            stub_node, cites_edge = self._make_citation_pair(paper_id, cit)
            if cites_edge:
                edges.append(cites_edge)
            if stub_node and stub_node.node_id not in existing_ids:
                nodes.append(stub_node)
                existing_ids.add(stub_node.node_id)

        # 7. Final deduplication (safety net)
        nodes = self._dedup_nodes(nodes)
        edges = self._dedup_edges(edges)

        logger.info(
            "paper_graph_built",
            paper_id=paper_id,
            nodes=len(nodes),
            edges=len(edges),
        )
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Node factories
    # ------------------------------------------------------------------

    def _make_paper_node(
        self,
        paper_id: str,
        title: Optional[str],
        abstract: Optional[str],
        authors: List[str],
        year: Optional[int],
    ) -> Node:
        props: Dict[str, Any] = {}
        if title:
            props["title"] = title
        if abstract:
            props["abstract"] = abstract[:2000]  # truncate for storage
        if year:
            props["year"] = year
        if authors:
            props["author_names"] = authors
        props["is_stub"] = False

        return Node(
            node_id=f"paper_{paper_id}",
            node_type="Paper",
            name=title or paper_id,
            paper_id=paper_id,
            properties=props,
        )

    @staticmethod
    def _make_author_node(name: str) -> Node:
        nid = f"author_{_stable_hash(name)}"
        return Node(
            node_id=nid,
            node_type="Author",
            name=name,
            paper_id="",  # authors are global, not scoped to one paper
            properties={"name": name},
        )

    @staticmethod
    def _make_section_node(paper_id: str, heading: str, text: str, index: int) -> Node:
        nid = f"section_{paper_id}_{_stable_hash(heading)}"
        props: Dict[str, Any] = {"heading": heading, "section_id": nid}
        if text:
            props["text_preview"] = text[:500]
        return Node(
            node_id=nid,
            node_type="Section",
            name=heading,
            paper_id=paper_id,
            properties=props,
        )

    def _make_entity_node(self, paper_id: str, ent: Dict[str, str]) -> Optional[Node]:
        name = (ent.get("name") or "").strip()
        etype = ent.get("type") or ""

        if not name or not self._validator.validate_node_type(etype):
            return None

        # Globally shared entities (same Transformer across papers)
        globally_shared = {"Method", "Dataset", "Task", "Metric"}

        if etype in globally_shared:
            nid = f"{etype.lower()}_{_stable_hash(name.lower())}"
        else:
            # Claim, Experiment are per-paper
            nid = f"{etype.lower()}_{paper_id}_{_stable_hash(name.lower())}"

        props = {
            "source_section": ent.get("source_section", ""),
            "evidence": ent.get("evidence", ""),
        }

        return Node(
            node_id=nid,
            node_type=etype,
            name=name,
            paper_id=paper_id,
            properties=props,
        )

    # ------------------------------------------------------------------
    # Edge factories
    # ------------------------------------------------------------------

    def _safe_edge(
        self,
        source_id: str,
        source_type: str,
        edge_type: str,
        target_id: str,
        target_type: str,
        evidence: Optional[str] = None,
        confidence: float = 1.0,
        properties: Optional[Dict] = None,
    ) -> Optional[Edge]:
        """Create an Edge, returning None when the ontology rejects it."""
        try:
            return Edge(
                source_id=source_id,
                source_type=source_type,
                edge_type=edge_type,
                target_id=target_id,
                target_type=target_type,
                evidence=evidence,
                confidence=confidence,
                properties=properties or {},
            )
        except ValueError:
            # Silently drop invalid edges (logged at DEBUG inside Edge.__init__ context)
            return None

    def _make_relation_edge(
        self,
        rel: Dict[str, str],
        entity_id_map: Dict[Tuple[str, str], str],
    ) -> Optional[Edge]:
        src_key = (rel.get("source_type", ""), (rel.get("source") or "").lower())
        tgt_key = (rel.get("target_type", ""), (rel.get("target") or "").lower())
        src_id = entity_id_map.get(src_key)
        tgt_id = entity_id_map.get(tgt_key)
        if not src_id or not tgt_id:
            return None
        return self._safe_edge(
            src_id,
            rel.get("source_type", ""),
            rel.get("relation", ""),
            tgt_id,
            rel.get("target_type", ""),
            evidence=rel.get("evidence"),
        )

    # ------------------------------------------------------------------
    # Citation handling  (stub nodes for cross-paper linking)
    # ------------------------------------------------------------------

    def _make_citation_pair(
        self,
        paper_id: str,
        citation: Dict[str, Any],
    ) -> Tuple[Optional[Node], Optional[Edge]]:
        """
        Create a (stub Paper node, CITES edge) for a single citation.

        Citation resolution priority for stable IDs:
          1. DOI           -- most reliable persistent identifier
          2. arXiv ID      -- second best
          3. Title hash    -- fallback when neither DOI nor arXiv available

        If none of title / DOI / arXiv are available the citation is skipped
        entirely (nothing returned).
        """
        title = citation.get("title")
        doi = citation.get("doi")
        arxiv_id = citation.get("arxiv_id")
        ref_id = citation.get("ref_id", "")

        if not title and not doi and not arxiv_id:
            return None, None

        # --- stable paper ID for the cited work ---
        if doi:
            cited_pid = f"doi_{doi.lower()}"
        elif arxiv_id:
            cited_pid = f"arxiv_{arxiv_id.lower()}"
        else:
            cited_pid = f"ref_{paper_id}_{ref_id or _stable_hash(title or 'unknown')}"

        cited_nid = f"paper_{cited_pid}"

        # --- stub properties ---
        stub_props: Dict[str, Any] = {
            "is_stub": True,
            "source_paper_id": paper_id,
        }
        if title:
            stub_props["title"] = title
        if doi:
            stub_props["doi"] = doi
        if arxiv_id:
            stub_props["arxiv_id"] = arxiv_id
        year = citation.get("year")
        if year:
            stub_props["year"] = year
        auths = citation.get("authors", [])
        if auths:
            stub_props["author_names"] = auths
        if ref_id:
            stub_props["ref_id"] = ref_id

        stub_node = Node(
            node_id=cited_nid,
            node_type="Paper",
            name=title or cited_pid,
            paper_id=cited_pid,
            properties=stub_props,
        )

        cites_edge = self._safe_edge(
            f"paper_{paper_id}", "Paper",
            "CITES",
            cited_nid, "Paper",
            evidence=f"Referenced as [{ref_id}]" if ref_id else None,
            confidence=0.9,
            properties={"ref_id": ref_id},
        )

        return stub_node, cites_edge

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_nodes(nodes: List[Node]) -> List[Node]:
        seen: set[str] = set()
        out: List[Node] = []
        for n in nodes:
            if n.node_id not in seen:
                seen.add(n.node_id)
                out.append(n)
        return out

    @staticmethod
    def _dedup_edges(edges: List[Edge]) -> List[Edge]:
        seen: set[tuple] = set()
        out: List[Edge] = []
        for e in edges:
            key = (e.source_id, e.edge_type, e.target_id)
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out