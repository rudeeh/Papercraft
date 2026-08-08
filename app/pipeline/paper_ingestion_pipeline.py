"""
Paper Ingestion Pipeline Module (Phase 10.1)

Orchestrates the complete flow from PDF file to stored knowledge graph
and vector embeddings.

Pipeline stages
---------------
PDF
  -> OCR  (critical)
  -> Paper Parsing  (critical)
  -> Citation Extraction  (non-critical)
  -> Entity Extraction  (non-critical)
  -> Relation Extraction  (non-critical)
  -> Build Paper Graph  (non-critical)
  -> Store Graph in Neo4j  (non-critical)
  -> Build Vector Chunks  (non-critical)
  -> Generate Embeddings  (non-critical)
  -> Store Vectors in Weaviate  (non-critical)

Vector chunks are built from the same paper-graph inputs as the Neo4j
step (abstract, sections, entities) rather than raw OCR text, so every
stored vector carries ``node_type`` / ``node_name`` metadata linking it
back to its paper-graph node (Phase 9).

Risk Mitigations Addressed
--------------------------
- No error recovery: Every non-critical step is wrapped in try/except.
  Failure is logged and the pipeline continues, so a graph extraction
  failure does not block the existing vector-RAG path.
- Per-step status tracking: ``PipelineResult.steps`` records each step's
  outcome (SUCCESS / ERROR / SKIPPED), duration, and error message.
- Neo4j unavailability is graceful: If Neo4j cannot be reached the
  pipeline still completes the vector path and marks graph steps as SKIPPED.
- Weaviate unavailability is graceful: mirrors the Neo4j behaviour -- if no
  ``VectorRepository`` is supplied, EMBEDDING/VECTOR_STORE are SKIPPED
  rather than raising.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.paper.parser import PaperParser
from app.citations.extractor import CitationExtractor
from app.citations.normalizer import CitationNormalizer
from app.graph.entity_extractor import EntityExtractor
from app.graph.relation_extractor import RelationExtractor
from app.graph.paper_graph_builder import PaperGraphBuilder
from app.storage.neo4j_client import Neo4jClient
from app.storage.graph_repository import GraphRepository
from app.storage.vector_repository import VectorRepository
from app.core.config import settings

logger = structlog.get_logger()


# ======================================================================
# Result types
# ======================================================================

class StepStatus(str, Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    ERROR = "error"


def _elapsed_ms(start: float) -> float:
    """
    Milliseconds since *start* (a ``time.perf_counter()`` reading).

    Deliberately returns a float rounded to microseconds rather than an
    int: several steps legitimately finish in well under a millisecond,
    and truncating those to ``int`` reported them as a flat ``0ms``,
    which read as "the timer is broken" and made the per-step numbers
    useless for spotting which stage actually costs time.
    """
    return round((time.perf_counter() - start) * 1000, 3)


@dataclass
class StepResult:
    """Outcome of a single pipeline step."""
    step_name: str
    status: StepStatus
    data: Any = None
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class PipelineResult:
    """Outcome of the full ingestion pipeline."""

    paper_id: str
    steps: List[StepResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    # Aggregate counts for the return value
    graph_nodes_count: int = 0
    graph_edges_count: int = 0
    vector_count: int = 0
    citation_count: int = 0
    entity_count: int = 0
    relation_count: int = 0

    # ---- derived status --------------------------------------------------
    _CRITICAL_STEPS = frozenset({"OCR", "PARSING"})

    @property
    def status(self) -> str:
        """
        ``COMPLETED``  -- every step succeeded
        ``PARTIAL``    -- critical steps ok, some non-critical failed
        ``FAILED``     -- a critical step failed
        """
        critical_failures = [
            s for s in self.steps
            if s.step_name in self._CRITICAL_STEPS and s.status == StepStatus.ERROR
        ]
        if critical_failures:
            return "FAILED"

        any_error = any(s.status == StepStatus.ERROR for s in self.steps)
        return "PARTIAL" if any_error else "COMPLETED"

    def get_step(self, name: str) -> Optional[StepResult]:
        for s in self.steps:
            if s.step_name == name:
                return s
        return None


# ======================================================================
# Pipeline
# ======================================================================

class PaperIngestionPipeline:
    """
    End-to-end pipeline: PDF  ->  knowledge graph in Neo4j  +  vectors in Weaviate.
    """

    def __init__(
        self,
        neo4j_client: Optional[Neo4jClient] = None,
        vector_repo: Optional[VectorRepository] = None,
    ) -> None:
        self._neo4j_client = neo4j_client
        self._graph_repo: Optional[GraphRepository] = None
        self._vector_repo = vector_repo

        # Existing extractors (already in the codebase)
        self._parser = PaperParser()
        self._citation_extractor = CitationExtractor()
        self._citation_normalizer = CitationNormalizer()
        self._entity_extractor = EntityExtractor()
        self._relation_extractor = RelationExtractor()
        self._graph_builder = PaperGraphBuilder()

        if neo4j_client is not None:
            self._graph_repo = GraphRepository(neo4j_client)

    # ------------------------------------------------------------------
    # Internal step runner
    # ------------------------------------------------------------------

    @staticmethod
    def _run_step(
        step_name: str,
        fn,
        *args,
        critical: bool = False,
        **kwargs,
    ) -> StepResult:
        """Execute *fn* with timing and error handling."""
        # perf_counter, not time(): monotonic and high-resolution, where
        # time() can sit at ~15ms granularity on Windows -- enough on its
        # own to report a fast step as 0ms.
        t0 = time.perf_counter()
        try:
            data = fn(*args, **kwargs)
            return StepResult(
                step_name=step_name,
                status=StepStatus.SUCCESS,
                data=data,
                duration_ms=_elapsed_ms(t0),
            )
        except Exception as exc:
            ms = _elapsed_ms(t0)
            logger.error(
                "pipeline_step_failed",
                step=step_name,
                error=str(exc),
                critical=critical,
            )
            if critical:
                raise
            return StepResult(
                step_name=step_name,
                status=StepStatus.ERROR,
                error=str(exc),
                duration_ms=ms,
            )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, paper_id: str, file_path: str) -> PipelineResult:
        """
        Ingest one PDF end-to-end.

        Critical steps (OCR, PARSING) raise on failure.
        All other steps catch their own exceptions and continue.
        """
        t_start = time.perf_counter()
        result = PipelineResult(paper_id=paper_id)

        # ---- 1. OCR (CRITICAL) ------------------------------------------
        from app.services.ocr import extract_text_from_pdf

        ocr_step = self._run_step("OCR", extract_text_from_pdf, file_path, critical=True)
        result.steps.append(ocr_step)
        if ocr_step.status == StepStatus.ERROR:
            result.total_duration_ms = _elapsed_ms(t_start)
            return result
        pages_text: list = ocr_step.data  # [(page_num, text), ...]

        # ---- 2. Paper Parsing (CRITICAL) --------------------------------
        parse_step = self._run_step("PARSING", self._parser.parse, pages_text, critical=True)
        result.steps.append(parse_step)
        if parse_step.status == StepStatus.ERROR:
            result.total_duration_ms = _elapsed_ms(t_start)
            return result
        parsed = parse_step.data

        # ---- 3. Citation Extraction (non-critical) ----------------------
        full_text = "\n\n".join(text for _, text in pages_text)

        def _do_citations():
            raw = self._citation_extractor.extract(full_text)
            return self._citation_normalizer.normalize_list(raw["references"])

        cite_step = self._run_step("CITATIONS", _do_citations)
        result.steps.append(cite_step)
        citations = cite_step.data if cite_step.status == StepStatus.SUCCESS else []
        result.citation_count = len(citations)

        # ---- 4. Entity Extraction (non-critical) ------------------------
        def _do_entities():
            return self._entity_extractor.extract(parsed.to_dict())

        ent_step = self._run_step("ENTITIES", _do_entities)
        result.steps.append(ent_step)
        entities = ent_step.data if ent_step.status == StepStatus.SUCCESS else []
        result.entity_count = len(entities)

        # ---- 5. Relation Extraction (non-critical) ----------------------
        def _do_relations():
            return self._relation_extractor.extract(entities, parsed)

        rel_step = self._run_step("RELATIONS", _do_relations)
        result.steps.append(rel_step)
        relations = rel_step.data if rel_step.status == StepStatus.SUCCESS else []
        result.relation_count = len(relations)

        # ---- 6. Build Paper Graph (non-critical) ------------------------
        def _do_build_graph():
            return self._graph_builder.build(
                paper_id=paper_id,
                title=parsed.title,
                abstract=parsed.abstract,
                sections=parsed.sections,
                entities=entities,
                relations=relations,
                citations=citations,
                arxiv_id=parsed.arxiv_id,
                doi=parsed.doi,
            )

        graph_step = self._run_step("GRAPH_BUILD", _do_build_graph)
        result.steps.append(graph_step)
        graph_data = graph_step.data if graph_step.status == StepStatus.SUCCESS else None

        # ---- 7. Store Graph in Neo4j (non-critical) --------------------
        if graph_data and self._graph_repo is not None:
            def _do_store_graph():
                return self._graph_repo.store_paper_graph(
                    paper_id, graph_data["nodes"], graph_data["edges"]
                )

            store_step = self._run_step("NEO4J_STORE", _do_store_graph)
            result.steps.append(store_step)
            if store_step.status == StepStatus.SUCCESS:
                result.graph_nodes_count = store_step.data.get("nodes_stored", 0)
                result.graph_edges_count = store_step.data.get("edges_stored", 0)

            # Attempt citation-stub resolution (both directions -- see
            # _try_resolve_stubs)
            self._try_resolve_stubs(paper_id, parsed, citations)

        elif self._graph_repo is None:
            result.steps.append(StepResult(
                step_name="NEO4J_STORE", status=StepStatus.SKIPPED,
                error="Neo4j client not configured",
            ))
        else:
            result.steps.append(StepResult(
                step_name="NEO4J_STORE", status=StepStatus.SKIPPED,
                error="Graph build did not produce data",
            ))

        # ---- 8. Build Vector Chunks (non-critical) ----------------------
        def _do_chunking():
            chunks = self._build_vector_chunks(parsed, entities)
            if chunks:
                return chunks
            # No abstract/sections/entities were recognized (e.g. a plain
            # document with none of the academic section headings the
            # parser looks for) -- fall back to chunking the raw OCR text
            # directly, so the document still ends up searchable instead
            # of silently indexing zero chunks.
            return self._build_raw_text_chunks(pages_text)

        chunk_step = self._run_step("CHUNKING", _do_chunking)
        result.steps.append(chunk_step)
        chunks = chunk_step.data if chunk_step.status == StepStatus.SUCCESS else []

        # ---- 9. Generate Embeddings (non-critical) ----------------------
        vectors: Optional[List[List[float]]] = None
        if self._vector_repo is None:
            result.steps.append(StepResult(
                step_name="EMBEDDING", status=StepStatus.SKIPPED,
                error="Vector store not configured",
            ))
        elif not chunks:
            result.steps.append(StepResult(
                step_name="EMBEDDING", status=StepStatus.SKIPPED,
                error="No chunks to embed",
            ))
        else:
            def _do_embed():
                return self._vector_repo.embedder.embed([c["text"] for c in chunks])

            embed_step = self._run_step("EMBEDDING", _do_embed)
            result.steps.append(embed_step)
            vectors = embed_step.data if embed_step.status == StepStatus.SUCCESS else None

        # ---- 10. Store Vectors in Weaviate (non-critical) ---------------
        if vectors and self._vector_repo is not None:
            def _do_vector_store():
                return self._vector_repo.store_embedded_chunks(paper_id, chunks, vectors)

            vector_step = self._run_step("VECTOR_STORE", _do_vector_store)
            result.steps.append(vector_step)
            result.vector_count = (
                vector_step.data.get("chunks_stored", 0)
                if vector_step.status == StepStatus.SUCCESS else 0
            )
        else:
            result.steps.append(StepResult(
                step_name="VECTOR_STORE", status=StepStatus.SKIPPED,
                error="No embeddings to store",
            ))

        result.total_duration_ms = _elapsed_ms(t_start)
        logger.info(
            "pipeline_complete",
            paper_id=paper_id,
            status=result.status,
            steps=len(result.steps),
            duration_ms=result.total_duration_ms,
            graph_nodes=result.graph_nodes_count,
            graph_edges=result.graph_edges_count,
            vectors=result.vector_count,
        )
        return result

    # ------------------------------------------------------------------
    # Vector chunk construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_vector_chunks(parsed, entities: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """
        Build the list of text chunks to embed, each tagged with the
        paper-graph node it corresponds to (Phase 9 metadata schema:
        paper_id / section / node_type / node_name / source_text).
        """
        chunks: List[Dict[str, Any]] = []

        if parsed.abstract:
            chunks.append({
                "text": parsed.abstract,
                "section": "Abstract",
                "node_type": "Paper",
                "node_name": parsed.title or "",
                "source_text": parsed.abstract,
            })

        for sec in parsed.sections:
            text = (sec.get("text") or "").strip()
            if not text:
                continue
            heading = sec.get("heading", "")
            chunks.append({
                "text": text,
                "section": heading,
                "node_type": "Section",
                "node_name": heading,
                "source_text": text,
            })

        for ent in entities:
            name = ent.get("name")
            if not name:
                continue
            evidence = ent.get("evidence") or ""
            chunks.append({
                "text": f"{name}: {evidence}" if evidence else name,
                "section": ent.get("source_section", ""),
                "node_type": ent.get("type", ""),
                "node_name": name,
                "source_text": evidence,
            })

        return chunks

    @staticmethod
    def _build_raw_text_chunks(raw_pages: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
        """
        Fallback chunking over raw OCR page text, word-windowed by
        ``CHUNK_TOKENS``/``CHUNK_OVERLAP_TOKENS``. Used only when structured
        extraction (abstract/sections/entities) yields nothing to embed.
        """
        chunk_size = settings.CHUNK_TOKENS
        overlap = settings.CHUNK_OVERLAP_TOKENS
        chunks: List[Dict[str, Any]] = []

        for page_num, text in raw_pages:
            words = text.split()
            if not words:
                continue
            i = 0
            while i < len(words):
                end = min(i + chunk_size, len(words))
                chunk_str = " ".join(words[i:end])
                chunks.append({
                    "text": chunk_str,
                    "section": "Raw Text",
                    "node_type": "Section",
                    "node_name": f"page_{page_num}",
                    "source_text": chunk_str,
                    "page": page_num,
                })
                i += (chunk_size - overlap) if chunk_size > overlap else 1

        return chunks

    # ------------------------------------------------------------------
    # Citation stub resolution
    # ------------------------------------------------------------------

    def _try_resolve_stubs(self, paper_id: str, parsed, citations: List[Dict]) -> None:
        """
        Resolve citation stubs in both possible ingestion orders:

        1. **Cited-paper-arrives-later**: an earlier-ingested paper already
           cited *this* paper, creating a stub for it keyed by DOI/arXiv ID.
           If this paper's own identity -- extracted by the parser from its
           first page, not from its citations -- matches that stub, rewire
           it to the real node just stored.
        2. **Citing-paper-arrives-later**: this paper cites another paper
           that already exists as a real (non-stub) node. Its graph was
           just built with a fresh stub for that citation (since
           ``PaperGraphBuilder`` has no Neo4j access to know better) --
           resolve that stub to the existing real node immediately instead
           of leaving a redundant stub alongside it.

        Direction 1 uses ``parsed.doi`` / ``parsed.arxiv_id`` (this paper's
        own identifiers) -- never this paper's citations, which would
        resolve stubs representing papers *it* cites, not itself, silently
        corrupting the graph (rewiring this paper's own outgoing CITES edge
        into a self-loop and deleting the stub for the paper it actually
        cites -- see docs/decisions.md).
        """
        if not self._graph_repo:
            return

        # 1. Does this paper's own identity resolve a pre-existing stub?
        self_doi = getattr(parsed, "doi", None)
        self_arxiv_id = getattr(parsed, "arxiv_id", None)
        try:
            if self_doi:
                self._graph_repo.resolve_citation_stub(f"doi_{self_doi.lower()}", paper_id)
            if self_arxiv_id:
                self._graph_repo.resolve_citation_stub(f"arxiv_{self_arxiv_id.lower()}", paper_id)
        except Exception as exc:
            logger.warning(
                "stub_resolution_failed", paper_id=paper_id, error=str(exc)
            )

        # 2. Do any of this paper's citations already have a real node?
        for cit in citations:
            cit_doi = cit.get("doi")
            cit_arxiv_id = cit.get("arxiv_id")
            try:
                if cit_doi:
                    real_id = self._graph_repo.find_real_paper_id(doi=cit_doi)
                    if real_id and real_id != paper_id:
                        self._graph_repo.resolve_citation_stub(f"doi_{cit_doi.lower()}", real_id)
                if cit_arxiv_id:
                    real_id = self._graph_repo.find_real_paper_id(arxiv_id=cit_arxiv_id)
                    if real_id and real_id != paper_id:
                        self._graph_repo.resolve_citation_stub(f"arxiv_{cit_arxiv_id.lower()}", real_id)
            except Exception as exc:
                logger.warning(
                    "cited_paper_stub_resolution_failed", paper_id=paper_id, error=str(exc)
                )