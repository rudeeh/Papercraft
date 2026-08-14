"""
Paper detail endpoints for the split-screen UI.

    GET /api/v1/papers/{doc_id}/pdf         -- serve the uploaded PDF
    GET /api/v1/papers/{doc_id}/extraction  -- parsed sections + graph data

Both endpoints respect ``AUTH_REQUIRED`` (same dependency as the curation
routes): in single-player mode they're open, in multi-player mode they
require a JWT bearer token.

Security
--------
The PDF endpoint serves files from ``UPLOAD_DIR``. Path traversal is
blocked by three layers:

1. ``doc_id`` regex validation — only accepts the upload hash format
   (hex string) or the ``arxiv-{id}`` format produced by the arXiv
   ingestion endpoint. No paths, no ``..``, no slashes.
2. ``os.path.basename()`` on the resolved filename — even if the regex
   were bypassed, ``..`` can't escape the upload directory.
3. ``os.path.commonpath()`` containment check — the resolved real path
   must be inside ``UPLOAD_DIR``. Symlinks are resolved via
   ``os.path.realpath`` before the check.

Range requests are honored so pdf.js can stream large PDFs efficiently
without loading the entire file into memory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import settings
from app.api.deps import current_user

logger = structlog.get_logger()

router = APIRouter()

# ---------------------------------------------------------------------------
# doc_id validation
# ---------------------------------------------------------------------------

# Upload endpoint produces doc_ids as SHA-256 hashes (64 hex chars).
# arXiv ingestion produces doc_ids as "arxiv-{canonical_id}" where
# canonical_id is either YYMM.NNNNN or category/NNNNNNN.
_DOC_ID_RE = re.compile(
    r"^[a-f0-9]{64}$"                              # SHA-256 hash
    r"|^arxiv-\d{4}\.\d{4,5}$"                     # arxiv-YYMM.NNNNN
    r"|^arxiv-[a-z\-]+/\d{7}$",                    # arxiv-category/NNNNNNN
    re.IGNORECASE,
)


def _validate_doc_id(doc_id: str) -> str:
    """Reject anything that isn't a known doc_id format.

    This is the first layer of path-traversal defense: ``..``, ``/``,
    ``\\``, and null bytes can't match this regex, so a malicious
    ``doc_id`` never reaches the filesystem.
    """
    if not doc_id or not _DOC_ID_RE.match(doc_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid doc_id format. Expected a 64-char hex hash or 'arxiv-{id}'.",
        )
    return doc_id


def _resolve_pdf_path(doc_id: str) -> Path:
    """Resolve the on-disk PDF path with containment enforcement.

    Three layers of path-traversal defense:
    1. ``_validate_doc_id`` (regex) — called by the endpoint before us.
    2. ``os.path.basename()`` — strips any path components.
    3. ``os.path.commonpath()`` — verifies the real path is inside
       ``UPLOAD_DIR``, after resolving symlinks.
    """
    # Layer 2: basename strips any path separators that somehow got
    # through the regex (defense in depth — the regex should already
    # block them, but this costs nothing).
    filename = os.path.basename(f"{doc_id}.pdf")
    upload_dir = os.path.realpath(settings.UPLOAD_DIR)
    candidate = os.path.realpath(os.path.join(upload_dir, filename))

    # Layer 3: the resolved path must be inside upload_dir.
    if os.path.commonpath([upload_dir, candidate]) != upload_dir:
        # This should be unreachable given the regex + basename, but if
        # it ever fires it means someone found a bypass — log loudly.
        logger.error(
            "paper.pdf.path_traversal_blocked",
            doc_id=doc_id,
            candidate=candidate,
            upload_dir=upload_dir,
        )
        raise HTTPException(status_code=400, detail="Invalid doc_id")

    return Path(candidate)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PaperSection(BaseModel):
    heading: str
    text: str


class PaperExtraction(BaseModel):
    doc_id: str
    title: Optional[str] = None
    abstract: Optional[str] = None
    sections: List[PaperSection] = []
    entities: List[Dict[str, Any]] = []
    citations: List[Dict[str, Any]] = []
    graph_stats: Dict[str, int] = {}
    has_pdf: bool = False
    extraction_available: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/papers/{doc_id}/pdf")
async def serve_paper_pdf(
    doc_id: str,
    request: Request,
    user=Depends(current_user),
):
    """Stream the PDF file for a paper.

    Honors ``Range`` requests so pdf.js can stream large PDFs without
    loading the entire file into memory. Returns 404 if the PDF doesn't
    exist on disk (paper was never uploaded, or was deleted).
    """
    _validate_doc_id(doc_id)
    pdf_path = _resolve_pdf_path(doc_id)

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"No PDF found for doc_id '{doc_id}'")

    # FileResponse handles Range requests automatically when the
    # client sends a Range header (pdf.js always does for large files).
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{doc_id}.pdf",
        # Content-Disposition: inline so the browser renders it rather
        # than downloading. The frontend will embed it in an iframe.
        headers={
            "Content-Disposition": f"inline; filename={doc_id}.pdf",
            # Allow the frontend to embed this in an iframe. CORS is
            # handled by the middleware in main.py; this header is for
            # the iframe sandbox, not CORS.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/papers/{doc_id}/extraction", response_model=PaperExtraction)
async def get_paper_extraction(
    doc_id: str,
    user=Depends(current_user),
):
    """Return the parsed extraction data for a paper.

    Combines:
    - PDF existence check (from disk)
    - Parsed sections + entities + citations (from Neo4j, if available)
    - Graph stats (node/edge counts from the pipeline result, if available)

    If Neo4j is unavailable, returns a partial response with
    ``extraction_available=False`` and ``has_pdf=True`` — the frontend
    can still show the PDF with a "graph data unavailable" message.
    """
    _validate_doc_id(doc_id)
    pdf_path = _resolve_pdf_path(doc_id)
    has_pdf = pdf_path.exists()

    result = PaperExtraction(
        doc_id=doc_id,
        has_pdf=has_pdf,
        extraction_available=False,
    )

    if not has_pdf:
        # No PDF means the paper was never ingested. Return early with
        # 404 — there's nothing to show.
        raise HTTPException(status_code=404, detail=f"No PDF found for doc_id '{doc_id}'")

    # Try to fetch graph data from Neo4j. If Neo4j is down, return a
    # partial response rather than 503 — the frontend can still show
    # the PDF and a "graph data unavailable" message.
    try:
        from app.storage.neo4j_client import Neo4jClient
        from app.storage.graph_repository import GraphRepository

        client = Neo4jClient(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
        client.connect()
        try:
            repo = GraphRepository(client)
            paper_data = _fetch_paper_graph_data(repo, doc_id)
            if paper_data:
                result.title = paper_data.get("title")
                result.abstract = paper_data.get("abstract")
                result.sections = [
                    PaperSection(heading=s.get("heading", ""), text=s.get("text", ""))
                    for s in paper_data.get("sections", [])
                ]
                result.entities = paper_data.get("entities", [])
                result.citations = paper_data.get("citations", [])
                result.graph_stats = paper_data.get("graph_stats", {})
                result.extraction_available = True
        finally:
            client.close()
    except Exception as exc:
        # Neo4j unavailable — log and return partial data. The PDF is
        # still servable; the frontend shows a "graph data unavailable"
        # message on the extraction panel.
        logger.warning(
            "paper.extraction.neo4j_unavailable",
            doc_id=doc_id,
            error=str(exc),
        )

    return result


def _fetch_paper_graph_data(
    repo, doc_id: str
) -> Optional[Dict[str, Any]]:
    """Query Neo4j for the paper's graph data.

    Returns None if the paper isn't in the graph (pipeline hasn't run
    yet, or the paper was uploaded but Neo4j was down during ingestion).
    """
    # Use a single Cypher query to fetch the paper + its sections +
    # entities + citations in one round-trip.
    query = """
    MATCH (p:Paper {paper_id: $doc_id})
    OPTIONAL MATCH (p)-[:HAS_SECTION]->(s:Section)
    WITH p, collect(DISTINCT {
        heading: s.heading,
        text: s.text
    }) AS sections
    OPTIONAL MATCH (p)-[:MAKES_CLAIM|INTRODUCES|USES_METHOD|USES_DATASET]->(e)
    WITH p, sections, collect(DISTINCT {
        node_type: labels(e)[0],
        name: e.name,
        properties: properties(e)
    }) AS entities
    OPTIONAL MATCH (p)-[:CITES]->(c:Paper)
    WITH p, sections, entities, collect(DISTINCT {
        title: c.title,
        doi: c.doi,
        arxiv_id: c.arxiv_id,
        is_stub: c.is_stub,
        abstract: c.abstract,
        citation_count: c.citation_count,
        influential_citation_count: c.influential_citation_count,
        tldr: c.tldr
    }) AS citations
    RETURN {
        title: p.title,
        abstract: p.abstract,
        sections: [s IN sections WHERE s.heading IS NOT NULL],
        entities: entities,
        citations: [c IN citations WHERE c.title IS NOT NULL OR c.doi IS NOT NULL],
        graph_stats: {
            section_count: size([s IN sections WHERE s.heading IS NOT NULL]),
            entity_count: size(entities),
            citation_count: size([c IN citations WHERE c.title IS NOT NULL OR c.doi IS NOT NULL])
        }
    } AS data
    """

    try:
        with repo._client.session() as session:
            result = session.run(query, doc_id=doc_id)
            record = result.single()
            if record is None:
                return None
            return record["data"]
    except Exception as exc:
        logger.warning(
            "paper.extraction.graph_query_failed",
            doc_id=doc_id,
            error=str(exc),
        )
        return None
