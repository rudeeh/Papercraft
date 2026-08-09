"""
POST /api/v1/ingest/arxiv

Accepts an arXiv id (e.g. ``2401.12345`` or its versioned / URL form),
enqueues :func:`app.worker.arxiv_tasks.process_arxiv_task`, and returns
the Celery task id so the caller can poll ``GET /api/v1/status/{task_id}``
exactly as for an upload.

Designed to mirror the shape of ``POST /api/v1/upload``: same rate limit,
same idempotent doc_id strategy (``arxiv-{id}``), same status polling
contract.
"""

# NOTE: deliberately NOT using `from __future__ import annotations` here.
# FastAPI needs to resolve the `body: ArxivIngestRequest` annotation at
# decorator-eval time so it can mark the parameter as a body field, and
# PEP 563 deferred strings defeat that detection (the parameter gets
# misclassified as a query param). The chat endpoint in app/api/routes.py
# uses the same pattern for the same reason.

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.services.arxiv import InvalidArxivId, normalize_arxiv_id
from app.worker.celery_app import celery_app

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


class ArxivIngestRequest(BaseModel):
    """A single arXiv id in any of the forms accepted by
    :func:`app.services.arxiv.normalize_arxiv_id`."""

    arxiv_id: str

    @field_validator("arxiv_id")
    @classmethod
    def _must_be_valid_arxiv_id(cls, v: str) -> str:
        try:
            normalize_arxiv_id(v)
        except InvalidArxivId as exc:
            raise ValueError(f"not a recognizable arXiv id: {exc}") from exc
        return v.strip()


class ArxivIngestResponse(BaseModel):
    message: str
    arxiv_id: str          # canonical, no version
    doc_id: str            # "arxiv-{canonical_id}"
    task_id: str           # celery id, pollable via /status


@router.post("/ingest/arxiv", response_model=ArxivIngestResponse)
@limiter.limit("5/minute")
async def ingest_arxiv(request: Request, body: ArxivIngestRequest):
    """
    Enqueue an arXiv ingestion job. The endpoint validates the id
    synchronously (cheap regex) and rejects malformed input with 422
    before any task is dispatched. The actual download + pipeline run
    happens asynchronously in the worker.
    """
    canonical_id, _version = normalize_arxiv_id(body.arxiv_id)
    doc_id = f"arxiv-{canonical_id}"

    task = celery_app.send_task(
        "app.worker.tasks.process_arxiv_task",
        args=[body.arxiv_id],
    )

    # Mirror upload_pdf()'s pre-flight PENDING store so /status can
    # distinguish a queued task from a typoed id.
    try:
        celery_app.backend.store_result(task.id, None, "PENDING")
    except Exception:
        pass  # non-fatal, see upload_pdf for rationale

    return ArxivIngestResponse(
        message="arXiv ingestion started.",
        arxiv_id=canonical_id,
        doc_id=doc_id,
        task_id=task.id,
    )
