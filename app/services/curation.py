"""
Curation engine: the draft queue, attestation voting, and promotion.

This is the human half of the README's "AI extracts structure; humans
attest, correct, and curate" loop. The confidence router decides what a
human never has to look at; everything else lands here as an
``ExtractionDraft`` and waits for votes.

Promotion is the only path by which a reviewed extraction reaches Neo4j.
It is deliberately idempotent and audited: promoting an already-promoted
draft is a no-op, and every state change writes an ``AuditLog`` row naming
the actor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import structlog
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Attestation,
    AuditLog,
    DraftKind,
    DraftStatus,
    ExtractionDraft,
    RoutingDecision,
    User,
)
from app.graph.confidence_router import RoutedExtraction

logger = structlog.get_logger()

# Reputation awarded to a curator whose vote agreed with the draft's final
# outcome. Losing votes cost nothing: penalising disagreement teaches
# curators to vote with the crowd, which is the opposite of what
# attestation is for.
REPUTATION_PER_CORRECT_VOTE = 1


class CurationError(RuntimeError):
    """Raised when a curation action is not valid for a draft's current state."""


# ======================================================================
# Queueing
# ======================================================================

def queue_drafts(
    session: Session,
    paper_id: str,
    routed: Iterable[RoutedExtraction],
    *,
    actor_id: Optional[str] = None,
) -> List[ExtractionDraft]:
    """
    Persist the routed extractions that need review.

    AUTO_INSERT and DISCARDED items are not stored: the first are already in
    the graph, and the second were rejected before they got here. Only the
    queue is written, so the table stays a to-do list rather than a log of
    everything the extractor ever thought.
    """
    drafts: List[ExtractionDraft] = []

    for item in routed:
        if not item.needs_review:
            continue
        draft = ExtractionDraft(
            paper_id=paper_id,
            kind=item.kind.value,
            payload=item.payload,
            confidence=item.confidence,
            routing=item.routing.value,
            status=DraftStatus.PENDING.value,
            extracted_by=item.extracted_by,
        )
        session.add(draft)
        drafts.append(draft)

    if drafts:
        session.flush()  # assign IDs before the audit rows reference them
        session.add(
            AuditLog(
                actor_id=actor_id,
                action="drafts.queued",
                target_type="paper",
                target_id=paper_id,
                detail={"count": len(drafts)},
            )
        )
        session.commit()
        logger.info("drafts_queued", paper_id=paper_id, count=len(drafts))

    return drafts


def list_drafts(
    session: Session,
    *,
    paper_id: Optional[str] = None,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    routing: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[ExtractionDraft]:
    query = session.query(ExtractionDraft)
    if paper_id:
        query = query.filter(ExtractionDraft.paper_id == paper_id)
    if status:
        query = query.filter(ExtractionDraft.status == status)
    if kind:
        query = query.filter(ExtractionDraft.kind == kind)
    if routing:
        query = query.filter(ExtractionDraft.routing == routing)

    # Lowest confidence first: the queue should surface what the machine was
    # least sure about, which is where a human's time is worth most.
    return (
        query.order_by(ExtractionDraft.confidence.asc(), ExtractionDraft.created_at.asc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )


def queue_stats(session: Session, *, paper_id: Optional[str] = None) -> Dict[str, Any]:
    query = session.query(ExtractionDraft.status, func.count(ExtractionDraft.id))
    if paper_id:
        query = query.filter(ExtractionDraft.paper_id == paper_id)
    counts = dict(query.group_by(ExtractionDraft.status).all())
    return {
        "pending": counts.get(DraftStatus.PENDING.value, 0),
        "promoted": counts.get(DraftStatus.PROMOTED.value, 0),
        "rejected": counts.get(DraftStatus.REJECTED.value, 0),
        "total": sum(counts.values()),
    }


# ======================================================================
# Attestation
# ======================================================================

def attest(
    session: Session,
    draft_id: str,
    user: User,
    vote: int,
    *,
    note: Optional[str] = None,
    auto_promote: bool = True,
) -> Dict[str, Any]:
    """
    Record (or change) one curator's vote on a draft.

    Re-voting updates the existing row rather than adding a second one, so
    a curator who changes their mind moves the score by the difference
    instead of double-counting.
    """
    if vote not in (1, -1):
        raise CurationError("vote must be +1 or -1")

    draft = session.get(ExtractionDraft, draft_id)
    if draft is None:
        raise CurationError(f"No such draft: {draft_id}")
    if draft.status != DraftStatus.PENDING.value:
        raise CurationError(f"Draft is already {draft.status}")

    existing = (
        session.query(Attestation)
        .filter(Attestation.draft_id == draft_id, Attestation.user_id == user.id)
        .one_or_none()
    )

    if existing is not None:
        if existing.vote == vote:
            return _attestation_result(session, draft, changed=False)
        existing.vote = vote
        existing.note = note
    else:
        session.add(Attestation(draft_id=draft_id, user_id=user.id, vote=vote, note=note))

    try:
        session.flush()
    except IntegrityError:
        # Two concurrent first-votes from the same user; the unique
        # constraint held, so treat it as an idempotent no-op.
        session.rollback()
        return _attestation_result(session, session.get(ExtractionDraft, draft_id), changed=False)

    draft.attestation_score = _recompute_score(session, draft_id)
    session.add(
        AuditLog(
            actor_id=user.id,
            action="draft.attested",
            target_type="draft",
            target_id=draft_id,
            detail={"vote": vote, "score": draft.attestation_score},
        )
    )
    session.commit()

    promoted = False
    if auto_promote and draft.attestation_score >= settings.ATTESTATION_PROMOTE_SCORE:
        promote_draft(session, draft_id, user, reason="attestation_threshold")
        promoted = True
    elif auto_promote and draft.attestation_score <= -settings.ATTESTATION_PROMOTE_SCORE:
        reject_draft(session, draft_id, user, reason="attestation_threshold")

    result = _attestation_result(session, session.get(ExtractionDraft, draft_id), changed=True)
    result["promoted"] = promoted
    return result


def _recompute_score(session: Session, draft_id: str) -> int:
    """
    Sum the votes from the rows themselves rather than incrementing a
    counter, so the score can never drift out of step with the votes that
    justify it.
    """
    total = (
        session.query(func.coalesce(func.sum(Attestation.vote), 0))
        .filter(Attestation.draft_id == draft_id)
        .scalar()
    )
    return int(total or 0)


def _attestation_result(
    session: Session, draft: Optional[ExtractionDraft], *, changed: bool
) -> Dict[str, Any]:
    if draft is None:
        raise CurationError("Draft disappeared while voting")
    return {
        "draft": draft.to_dict(),
        "score": draft.attestation_score,
        "changed": changed,
        "promoted": draft.status == DraftStatus.PROMOTED.value,
    }


# ======================================================================
# Resolution
# ======================================================================

def promote_draft(
    session: Session,
    draft_id: str,
    actor: Optional[User],
    *,
    reason: str = "manual",
    graph_repository: Optional[Any] = None,
) -> ExtractionDraft:
    """
    Accept a draft and write it into the graph.

    If no ``graph_repository`` is supplied the draft is still marked
    promoted -- the audit row records that the graph write was deferred, and
    ``pending_graph_writes`` can replay it once Neo4j is reachable. Blocking
    promotion on Neo4j would mean a curator's review is lost whenever the
    graph is down.
    """
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None:
        raise CurationError(f"No such draft: {draft_id}")
    if draft.status == DraftStatus.PROMOTED.value:
        return draft  # Idempotent.
    if draft.status == DraftStatus.REJECTED.value:
        raise CurationError("Cannot promote a rejected draft")

    written = False
    if graph_repository is not None:
        try:
            _write_to_graph(graph_repository, draft)
            written = True
        except Exception as exc:
            logger.warning("draft_promotion_graph_write_failed", draft_id=draft_id, error=str(exc))

    draft.status = DraftStatus.PROMOTED.value
    draft.resolved_at = datetime.now(timezone.utc)
    draft.resolved_by = actor.id if actor else None

    session.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action="draft.promoted",
            target_type="draft",
            target_id=draft_id,
            detail={"reason": reason, "graph_written": written, "paper_id": draft.paper_id},
        )
    )
    _award_reputation(session, draft_id, winning_vote=1)
    session.commit()

    logger.info("draft_promoted", draft_id=draft_id, graph_written=written)
    return draft


def reject_draft(
    session: Session, draft_id: str, actor: Optional[User], *, reason: str = "manual"
) -> ExtractionDraft:
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None:
        raise CurationError(f"No such draft: {draft_id}")
    if draft.status == DraftStatus.REJECTED.value:
        return draft
    if draft.status == DraftStatus.PROMOTED.value:
        raise CurationError("Cannot reject an already-promoted draft")

    draft.status = DraftStatus.REJECTED.value
    draft.resolved_at = datetime.now(timezone.utc)
    draft.resolved_by = actor.id if actor else None

    session.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action="draft.rejected",
            target_type="draft",
            target_id=draft_id,
            detail={"reason": reason, "paper_id": draft.paper_id},
        )
    )
    _award_reputation(session, draft_id, winning_vote=-1)
    session.commit()

    logger.info("draft_rejected", draft_id=draft_id)
    return draft


def pending_graph_writes(session: Session, paper_id: Optional[str] = None) -> List[ExtractionDraft]:
    """
    Drafts promoted while Neo4j was unreachable, identified from the audit
    log rather than a flag column so the replay set is derived from the
    record of what actually happened.
    """
    deferred_ids = [
        row.target_id
        for row in session.query(AuditLog)
        .filter(AuditLog.action == "draft.promoted")
        .all()
        if not (row.detail or {}).get("graph_written")
    ]
    if not deferred_ids:
        return []

    query = session.query(ExtractionDraft).filter(ExtractionDraft.id.in_(deferred_ids))
    if paper_id:
        query = query.filter(ExtractionDraft.paper_id == paper_id)
    return query.all()


def _award_reputation(session: Session, draft_id: str, *, winning_vote: int) -> None:
    voters = (
        session.query(Attestation)
        .filter(Attestation.draft_id == draft_id, Attestation.vote == winning_vote)
        .all()
    )
    for attestation in voters:
        user = session.get(User, attestation.user_id)
        if user is not None:
            user.reputation += REPUTATION_PER_CORRECT_VOTE


def _write_to_graph(graph_repository: Any, draft: ExtractionDraft) -> None:
    """Hand a promoted draft's payload to the graph layer."""
    payload = draft.payload or {}
    if draft.kind == DraftKind.ENTITY.value:
        graph_repository.store_curated_entity(draft.paper_id, payload)
    elif draft.kind == DraftKind.RELATION.value:
        graph_repository.store_curated_relation(draft.paper_id, payload)
    else:
        raise CurationError(f"Unknown draft kind: {draft.kind}")


def leaderboard(session: Session, limit: int = 10) -> List[Dict[str, Any]]:
    users = (
        session.query(User)
        .filter(User.reputation > 0)
        .order_by(User.reputation.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    return [
        {"user_id": u.id, "display_name": u.display_name, "reputation": u.reputation}
        for u in users
    ]
