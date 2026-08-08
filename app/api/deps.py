"""
Shared FastAPI dependencies: database session and current-user resolution.

Auth is *optional by design*. README design principle 4 ("Single-Player
Mode First") means an individual researcher must be able to upload papers
and curate their own graph without signing up, so with
``AUTH_REQUIRED=false`` (the default) an unauthenticated caller is resolved
to a shared anonymous user rather than rejected. Flipping the flag to true
makes every curation route demand a bearer token, with no code change.
"""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import TokenError, decode_access_token
from app.db.base import get_session
from app.db.models import User

logger = structlog.get_logger()

ANONYMOUS_EMAIL = "anonymous@papercraft.local"
ANONYMOUS_NAME = "Anonymous Curator"


def db_session() -> Session:
    """
    Request-scoped session that reports an unreachable database as a 503
    rather than letting a driver error surface as a 500.
    """
    yield from _guarded_session()


def _guarded_session():
    try:
        generator = get_session()
    except SQLAlchemyError as exc:  # pragma: no cover - engine construction failure
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    session = next(generator)
    try:
        yield session
    finally:
        session.close()


def get_anonymous_user(session: Session) -> User:
    """
    Fetch (or lazily create) the shared anonymous curator.

    Created on demand instead of seeded at startup so a fresh database has
    no rows until someone actually curates something.
    """
    user = session.query(User).filter(User.email == ANONYMOUS_EMAIL).one_or_none()
    if user is None:
        user = User(
            email=ANONYMOUS_EMAIL,
            display_name=ANONYMOUS_NAME,
            # Not a valid PBKDF2 hash, so verify_password() can never match
            # it -- the anonymous account is unloggable-into by construction.
            password_hash="!",
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def _user_from_token(token: str, session: Session) -> User:
    try:
        claims = decode_access_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User no longer exists or is disabled")
    return user


def current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(db_session),
) -> User:
    """
    Resolve the acting user.

    With a bearer token: always the token's user (an invalid token is a 401
    even when AUTH_REQUIRED is false -- a *wrong* credential is an error,
    only a *missing* one falls back).
    Without one: the anonymous user, or 401 if AUTH_REQUIRED is set.
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Expected 'Bearer <token>' authorization")
        return _user_from_token(token.strip(), session)

    if settings.AUTH_REQUIRED:
        raise HTTPException(status_code=401, detail="Authentication required")

    return get_anonymous_user(session)


def require_authenticated_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(db_session),
) -> User:
    """Like ``current_user`` but never falls back to anonymous."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Expected 'Bearer <token>' authorization")
    return _user_from_token(token.strip(), session)
