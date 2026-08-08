"""
Authentication routes: register, login, me.

OAuth2 password flow with a JWT bearer token. ORCID sign-in (README's
stated end state) is not wired up here -- the ``orcid`` column exists on
User so linking one later is an update rather than a migration, but no
external identity provider is contacted.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import ANONYMOUS_EMAIL, current_user, db_session
from app.core.config import settings
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.models import AuditLog, User

logger = structlog.get_logger()

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)

    @field_validator("email")
    @classmethod
    def not_the_reserved_anonymous_address(cls, v: str) -> str:
        if v.lower() == ANONYMOUS_EMAIL:
            raise ValueError("that address is reserved")
        return v.lower()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, session: Session = Depends(db_session)):
    user = User(
        email=str(body.email).lower(),
        display_name=body.display_name.strip(),
        password_hash=hash_password(body.password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        # Relies on the unique index rather than a pre-check, so two
        # simultaneous registrations of the same address can't both pass.
        session.rollback()
        raise HTTPException(status_code=409, detail="That email is already registered")
    session.refresh(user)

    session.add(
        AuditLog(
            actor_id=user.id, action="user.register", target_type="user", target_id=user.id
        )
    )
    session.commit()

    logger.info("user_registered", user_id=user.id)
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(db_session)):
    user = session.query(User).filter(User.email == str(body.email).lower()).one_or_none()

    # Same message and roughly the same work for "no such user" and "wrong
    # password", so the endpoint doesn't become an account-enumeration oracle.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        session.commit()

    return _token_response(user)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user.to_dict()


def _token_response(user: User) -> dict:
    token = create_access_token(user.id, extra_claims={"email": user.email})
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": user.to_dict(),
    }
