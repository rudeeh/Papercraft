"""
Celery task: ingest a paper from arXiv by id.

Mirrors :func:`app.worker.tasks.process_pdf_task` but resolves the
arXiv id -> PDF bytes first, then runs the same downstream pipeline.
The downloaded PDF is stored under ``UPLOAD_DIR`` keyed by the
canonical arXiv id so a re-ingest with the same id is idempotent at
the file level (the pipeline still reprocesses, just like ``force=true``
on the upload endpoint).
"""

from __future__ import annotations

import os
from typing import Any

from app.core.config import settings
from app.worker.celery_app import celery_app
from app.worker.tasks import (
    _create_draft_sink,
    _create_llm_extractor,
    _create_neo4j_client,
    _create_openalex_client,
    _create_s2_client,
    _create_vector_repo,
    _record_job,
)
from celery.utils.log import get_task_logger
from app.services.arxiv import (
    ArxivClient,
    ArxivError,
    ArxivNotFound,
    ArxivUnavailable,
    InvalidArxivId,
    normalize_arxiv_id,
)
from app.pipeline.paper_ingestion_pipeline import PaperIngestionPipeline

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="app.worker.tasks.process_arxiv_task",
    max_retries=2,
    autoretry_for=(ArxivUnavailable,),
    retry_backoff=10,
    retry_jitter=True,
)
def process_arxiv_task(self, raw_arxiv_id: str) -> dict[str, Any]:
    """
    Download ``raw_arxiv_id`` from arXiv and run it through the full
    ingestion pipeline. Returns a payload shaped like
    :func:`process_pdf_task`'s return value, with an extra
    ``arxiv`` block carrying the resolved metadata.

    Failure modes that surface to the caller as ``status: "failed"``:
      * invalid id (no retry)
      * arXiv 404 / no entry (no retry)
      * any pipeline exception (no retry — pipeline already handles
        per-step errors internally and returns FAILED/PARTIAL)

    Only transient arXiv transport errors trigger a Celery retry.
    """
    try:
        arxiv_id, _version = normalize_arxiv_id(raw_arxiv_id)
    except InvalidArxivId as exc:
        logger.error("arxiv.ingest.invalid_id: %s", exc)
        return {
            "status": "failed",
            "error": f"invalid arXiv id: {exc}",
            "raw_id": raw_arxiv_id,
        }

    doc_id = f"arxiv-{arxiv_id}"
    file_path = os.path.join(settings.UPLOAD_DIR, f"{doc_id}.pdf")

    self.update_state(state="DOWNLOADING", meta={"step": "ARXIV_FETCH", "arxiv_id": arxiv_id})
    _record_job(doc_id, self.request.id, "downloading", filename=os.path.basename(file_path))

    # 1. Download (or reuse cached PDF from a previous attempt).
    if not os.path.exists(file_path):
        try:
            client = ArxivClient()
            meta = client.download_pdf(raw_arxiv_id, file_path)
        except (ArxivNotFound, InvalidArxivId) as exc:
            logger.error("arxiv.ingest.not_found: %s", exc)
            _record_job(doc_id, self.request.id, "failed", error=str(exc))
            return {"status": "failed", "error": str(exc), "arxiv_id": arxiv_id}
        except ArxivUnavailable as exc:
            # Let Celery retry via autoretry_for.
            logger.warning("arxiv.ingest.unavailable (will retry): %s", exc)
            _record_job(doc_id, self.request.id, "retrying", error=str(exc))
            raise
        except ArxivError as exc:
            logger.error("arxiv.ingest.error: %s", exc)
            _record_job(doc_id, self.request.id, "failed", error=str(exc))
            return {"status": "failed", "error": str(exc), "arxiv_id": arxiv_id}
    else:
        # File already on disk from a prior run — refetch metadata only.
        try:
            meta = ArxivClient().fetch_metadata(raw_arxiv_id)
        except ArxivError as exc:
            # Don't fail the whole task if a re-ingest can't refresh metadata;
            # the PDF is there and the pipeline can still run.
            logger.warning("arxiv.ingest.metadata_refresh_failed: %s", exc)
            meta = None  # type: ignore[assignment]

    # 2. Run the same pipeline as process_pdf_task.
    self.update_state(state="PROCESSING", meta={"step": "PIPELINE", "doc_id": doc_id})

    neo4j_client = _create_neo4j_client()
    vector_repo = _create_vector_repo()
    _record_job(doc_id, self.request.id, "processing", filename=os.path.basename(file_path))

    try:
        pipeline = PaperIngestionPipeline(
            neo4j_client=neo4j_client,
            vector_repo=vector_repo,
            openalex_client=_create_openalex_client(),
            llm_extractor=_create_llm_extractor(),
            draft_sink=_create_draft_sink(),
            s2_client=_create_s2_client(),
        )
        result = pipeline.process(paper_id=doc_id, file_path=file_path)
    except Exception as exc:
        logger.error("arxiv.ingest.pipeline_error: %s", exc)
        _record_job(doc_id, self.request.id, "failed", error=str(exc))
        if neo4j_client is not None:
            try:
                neo4j_client.close()
            except Exception:
                pass
        return {"status": "failed", "error": str(exc), "arxiv_id": arxiv_id}
    finally:
        if neo4j_client is not None:
            try:
                neo4j_client.close()
            except Exception:
                pass

    if result.status == "FAILED":
        _record_job(doc_id, self.request.id, "failed")
        return {
            "status": "failed",
            "arxiv_id": arxiv_id,
            "doc_id": doc_id,
            "steps": [
                {"step": s.step_name, "status": s.status.value, "error": s.error}
                for s in result.steps
            ],
        }

    payload = {
        "status": "completed" if result.status == "COMPLETED" else "partial",
        "arxiv_id": arxiv_id,
        "doc_id": doc_id,
        "arxiv": meta.to_dict() if meta else None,
        "chunks_count": result.vector_count,
        "citations_count": result.citation_count,
        "entities_count": result.entity_count,
        "relations_count": result.relation_count,
        "graph_nodes_count": result.graph_nodes_count,
        "graph_edges_count": result.graph_edges_count,
        "auto_inserted_count": result.auto_inserted_count,
        "queued_for_review_count": result.queued_for_review_count,
        "openalex_enriched": result.enriched,
        "pipeline_steps": [
            {"step": s.step_name, "status": s.status.value, "duration_ms": s.duration_ms}
            for s in result.steps
        ],
    }
    _record_job(doc_id, self.request.id, payload["status"], step_summary=payload["pipeline_steps"])
    return payload
