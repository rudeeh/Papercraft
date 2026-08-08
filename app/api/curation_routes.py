"""
Curation / attestation API (README Phase 1.4, "Curation Engine").

    GET   /curation/drafts              -- the review queue
    GET   /curation/drafts/{id}         -- one draft
    POST  /curation/drafts/{id}/attest  -- upvote / downvote
    POST  /curation/drafts/{id}/promote -- accept, write into the graph
    POST  /curation/drafts/{id}/reject  -- discard
    GET   /curation/stats               -- queue counts
    GET   /curation/leaderboard         -- top curators by reputation

Neo4j is resolved lazily and treated as optional: a promotion with the
graph down still records the human decision and is replayable later (see
``curation.pending_graph_writes``). Losing a curator's review because a
container was restarting would be the worse failure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.db.models import DraftKind, DraftStatus, ExtractionDraft, RoutingDecision, User
from app.services import curation
from app.services.curation import CurationError

logger = structlog.get_logger()

router = APIRouter(prefix="/curation", tags=["curation"])


class AttestRequest(BaseModel):
    vote: int = Field(description="+1 to attest, -1 to dispute")
    note: Optional[str] = Field(default=None, max_length=2000)


class ResolveRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


def _graph_repository():
    """
    Best-effort GraphRepository. Returns None when Neo4j is unreachable so
    the caller can still record the decision.
    """
    try:
        from app.api.graph_routes import get_graph_repository

        return get_graph_repository()
    except Exception as exc:
        logger.warning("curation_graph_unavailable", error=str(exc))
        return None


@router.get("/drafts")
def get_drafts(
    paper_id: Optional[str] = None,
    status: Optional[str] = Query(default=DraftStatus.PENDING.value),
    kind: Optional[str] = None,
    routing: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
) -> Dict[str, Any]:
    _validate_enum(status, DraftStatus, "status")
    _validate_enum(kind, DraftKind, "kind")
    _validate_enum(routing, RoutingDecision, "routing")

    drafts = curation.list_drafts(
        session,
        paper_id=paper_id,
        status=status,
        kind=kind,
        routing=routing,
        limit=limit,
        offset=offset,
    )
    return {"drafts": [d.to_dict() for d in drafts], "limit": limit, "offset": offset}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, session: Session = Depends(db_session)) -> Dict[str, Any]:
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"No such draft: {draft_id}")

    payload = draft.to_dict()
    payload["attestations"] = [a.to_dict() for a in draft.attestations]
    return payload


@router.post("/drafts/{draft_id}/attest")
def attest_draft(
    draft_id: str,
    body: AttestRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Dict[str, Any]:
    try:
        return curation.attest(session, draft_id, user, body.vote, note=body.note)
    except CurationError as exc:
        raise HTTPException(status_code=_curation_status(exc), detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/promote")
def promote(
    draft_id: str,
    body: ResolveRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Dict[str, Any]:
    try:
        draft = curation.promote_draft(
            session,
            draft_id,
            user,
            reason=(body.reason if body and body.reason else "manual"),
            graph_repository=_graph_repository(),
        )
    except CurationError as exc:
        raise HTTPException(status_code=_curation_status(exc), detail=str(exc)) from exc
    return draft.to_dict()


@router.post("/drafts/{draft_id}/reject")
def reject(
    draft_id: str,
    body: ResolveRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Dict[str, Any]:
    try:
        draft = curation.reject_draft(
            session, draft_id, user, reason=(body.reason if body and body.reason else "manual")
        )
    except CurationError as exc:
        raise HTTPException(status_code=_curation_status(exc), detail=str(exc)) from exc
    return draft.to_dict()


@router.get("/stats")
def stats(
    paper_id: Optional[str] = None, session: Session = Depends(db_session)
) -> Dict[str, Any]:
    return curation.queue_stats(session, paper_id=paper_id)


@router.get("/leaderboard")
def get_leaderboard(
    limit: int = Query(default=10, ge=1, le=100), session: Session = Depends(db_session)
) -> Dict[str, List[Dict[str, Any]]]:
    return {"curators": curation.leaderboard(session, limit=limit)}


def _validate_enum(value: Optional[str], enum_cls, field_name: str) -> None:
    """
    Reject an unknown filter value instead of silently returning nothing --
    a typo'd `status=pendign` that yields an empty list reads as "the queue
    is clear", which is the opposite of the truth.
    """
    if value is None:
        return
    allowed = {member.value for member in enum_cls}
    if value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {field_name} '{value}'. Expected one of: {sorted(allowed)}",
        )


def _curation_status(exc: CurationError) -> int:
    return 404 if str(exc).startswith("No such draft") else 409
