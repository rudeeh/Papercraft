"""
SQLAlchemy engine / session plumbing.

The engine is created lazily on first use rather than at import time. That
matters because every other datastore in this codebase (Neo4j, Weaviate) is
optional -- the worker and API boot and degrade gracefully when they are
unreachable -- and importing ``app.api.main`` must not fail just because
PostgreSQL happens to be down. ``get_session`` surfaces an unreachable
database as a 503 at the one route that needed it, not as a dead process.

Schema management is ``Base.metadata.create_all`` rather than Alembic. The
tables here are append-mostly bookkeeping (jobs, drafts, votes, audit rows)
with no production data to migrate yet; see docs/decisions.md for when that
should change.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

import structlog
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = structlog.get_logger()


class Base(DeclarativeBase):
    """Declarative base for every ORM model in app/db/models.py."""


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine(uri: Optional[str] = None) -> Engine:
    """
    Return the process-wide engine, creating it on first call.

    ``pool_pre_ping`` is on because the API and worker are long-lived and
    PostgreSQL (or a proxy in front of it) will happily close idle
    connections out from under them; without it the first query after an
    idle period fails with a stale-connection error.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            uri or settings.POSTGRES_URI,
            echo=settings.DB_ECHO,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autocommit=False, autoflush=False, future=True
        )
    return _session_factory


def configure_engine(engine: Engine) -> None:
    """
    Point the module at an already-built engine (tests use an in-memory
    SQLite one). Resets the cached session factory so it re-binds.
    """
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, future=True
    )


def reset_engine() -> None:
    """Drop the cached engine/session factory (used by tests for teardown)."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


def init_db() -> bool:
    """
    Create any missing tables. Returns True on success, False if the
    database is unreachable -- callers log and carry on rather than
    aborting startup.
    """
    # Imported for the side effect of registering the models on Base.metadata.
    from app.db import models  # noqa: F401

    try:
        Base.metadata.create_all(bind=get_engine())
        logger.info("postgres_schema_ready")
        return True
    except SQLAlchemyError as exc:
        logger.warning("postgres_unavailable", error=str(exc))
        return False


@contextmanager
def session_scope() -> Iterator[Session]:
    """
    Transactional scope for non-request callers (Celery tasks, scripts).
    Commits on clean exit, rolls back on exception, always closes.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
