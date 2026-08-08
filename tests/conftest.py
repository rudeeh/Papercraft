"""
Shared fixtures.

The database fixtures bind the app's engine to an in-memory SQLite
database. SQLite is used deliberately: every model in app/db/models.py is
written to the portable subset (String PKs, ``JSON`` rather than ``JSONB``,
no server-side defaults), so if a model ever drifts into PostgreSQL-only
territory these tests fail rather than passing until deploy.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db_engine():
    """In-memory SQLite engine with the full schema created."""
    from app.db.base import Base, configure_engine, reset_engine
    from app.db import models  # noqa: F401  (registers the tables)

    engine = create_engine(
        "sqlite://",
        # A single shared connection: ":memory:" is per-connection, so a
        # normal pool would hand out empty databases.
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # SQLite ignores foreign keys unless asked; without this the
    # ON DELETE CASCADE behaviour the models rely on wouldn't be exercised.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    configure_engine(engine)
    try:
        yield engine
    finally:
        reset_engine()
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    from app.db.base import get_session_factory

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def make_user(db_session):
    """Factory for persisted users."""
    from app.core.security import hash_password
    from app.db.models import User

    created = []

    def _make(email: str = "curator@example.com", name: str = "Curator", password: str = "hunter2!"):
        user = User(email=email, display_name=name, password_hash=hash_password(password))
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        created.append(user)
        return user

    return _make


@pytest.fixture
def make_draft(db_session):
    """Factory for persisted pending extraction drafts."""
    from app.db.models import DraftKind, DraftStatus, ExtractionDraft, RoutingDecision

    def _make(
        paper_id: str = "paper_1",
        kind: DraftKind = DraftKind.ENTITY,
        payload: dict | None = None,
        confidence: float = 0.6,
        routing: RoutingDecision = RoutingDecision.DRAFT,
    ):
        draft = ExtractionDraft(
            paper_id=paper_id,
            kind=kind.value,
            payload=payload
            or {
                "name": "Transformer",
                "type": "Method",
                "evidence": "The Transformer is the first transduction model relying entirely on attention.",
            },
            confidence=confidence,
            routing=routing.value,
            status=DraftStatus.PENDING.value,
            extracted_by="extraction-test",
        )
        db_session.add(draft)
        db_session.commit()
        db_session.refresh(draft)
        return draft

    return _make
