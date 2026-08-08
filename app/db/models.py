"""
Relational models.

Deliberately *not* a mirror of the knowledge graph. Neo4j owns the graph;
these tables own the things a graph is bad at: who a user is, what a job is
doing, which extractions are still awaiting human judgement, and an
append-only record of who changed what.

``JSON`` (not ``JSONB``) is used for payload columns so the same models run
against SQLite in the test suite and PostgreSQL in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ======================================================================
# Enumerations (stored as plain strings -- see docs/decisions.md on why we
# avoid native PG enums: adding a value to one requires a migration, and
# routing/status vocabularies here are still moving)
# ======================================================================

class DraftKind(str, PyEnum):
    ENTITY = "entity"
    RELATION = "relation"


class RoutingDecision(str, PyEnum):
    """Where the confidence router sent an extraction."""

    AUTO_INSERT = "auto_insert"  # high confidence -> straight into Neo4j
    DRAFT = "draft"              # medium -> queued for attestation
    MANUAL = "manual"            # low -> queued, flagged as needing a human
    DISCARDED = "discarded"      # below the floor -> never stored


class DraftStatus(str, PyEnum):
    PENDING = "pending"
    PROMOTED = "promoted"    # accepted and written into the graph
    REJECTED = "rejected"


class JobStatus(str, PyEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


# ======================================================================
# Models
# ======================================================================

class User(Base):
    """
    A curator. ``reputation`` accumulates from attestations that end up
    agreeing with the final outcome -- see app/services/curation.py.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    reputation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    orcid: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    attestations: Mapped[List["Attestation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "reputation": self.reputation,
            "is_active": self.is_active,
            "orcid": self.orcid,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IngestionJob(Base):
    """
    One PDF's trip through the pipeline. Celery's result backend already
    holds transient task state; this is the durable record that survives a
    Redis flush and gives ``GET /papers`` something to list.
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="upload")
    filename: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    object_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    step_summary: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "task_id": self.task_id,
            "status": self.status,
            "source": self.source,
            "filename": self.filename,
            "object_key": self.object_key,
            "error": self.error,
            "step_summary": self.step_summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ExtractionDraft(Base):
    """
    An extraction the confidence router did *not* wave straight through.

    ``payload`` holds the entity or relation dict exactly as the extractor
    produced it, so promoting a draft needs no re-extraction: the curation
    service hands the same dict to PaperGraphBuilder/GraphRepository that
    the pipeline would have.
    """

    __tablename__ = "extraction_drafts"
    __table_args__ = (
        Index("ix_drafts_paper_status", "paper_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    routing: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DraftStatus.PENDING.value, index=True
    )
    extracted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    attestations: Mapped[List["Attestation"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "kind": self.kind,
            "payload": self.payload,
            "confidence": self.confidence,
            "routing": self.routing,
            "status": self.status,
            "extracted_by": self.extracted_by,
            "attestation_score": self.attestation_score,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class Attestation(Base):
    """
    One curator's vote on one draft. The unique constraint is what makes
    ``attestation_score`` trustworthy -- without it a single user could
    upvote a draft to promotion on their own.
    """

    __tablename__ = "attestations"
    __table_args__ = (
        UniqueConstraint("draft_id", "user_id", name="uq_attestation_draft_user"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    draft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("extraction_drafts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    vote: Mapped[int] = mapped_column(Integer, nullable=False)  # +1 or -1
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    draft: Mapped["ExtractionDraft"] = relationship(back_populates="attestations")
    user: Mapped["User"] = relationship(back_populates="attestations")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "draft_id": self.draft_id,
            "user_id": self.user_id,
            "vote": self.vote,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditLog(Base):
    """
    Append-only provenance trail. The README's "Attribution Over Authority"
    principle needs every graph mutation to be answerable for, including
    the ones made by the pipeline itself (``actor_id`` NULL = the system).
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
