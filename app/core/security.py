"""
Password hashing and JWT issuing/verification.

Hashing is PBKDF2-HMAC-SHA256 from the standard library rather than bcrypt
or argon2. Those are stronger per-unit-cost, but both arrive via C
extensions whose wheels have repeatedly broken this project's slim
python:3.11 image, and the passlib/bcrypt 4.x pairing is a well-known
breakage. PBKDF2 with a high iteration count is an accepted choice
(NIST SP 800-63B), has zero install surface, and the stored format carries
its own parameters so raising the cost later is a per-user lazy rehash, not
a migration. See docs/decisions.md.

Stored format:  pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from app.core.config import settings

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 390_000
SALT_BYTES = 16
JWT_ALGORITHM = "HS256"


class TokenError(ValueError):
    """Raised when a token is malformed, mis-signed, or expired."""


# ======================================================================
# Passwords
# ======================================================================

def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    Constant-time check of *password* against a stored hash. Returns False
    (never raises) on a malformed hash, so a corrupt row is a failed login
    rather than a 500.
    """
    try:
        algorithm, iterations_s, salt_hex, digest_hex = stored.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_s)
        )
    except (ValueError, AttributeError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def needs_rehash(stored: str, *, iterations: int = PBKDF2_ITERATIONS) -> bool:
    """True if *stored* was made with a weaker cost than we now use."""
    try:
        algorithm, iterations_s, _, _ = stored.split("$")
    except (ValueError, AttributeError):
        return True
    return algorithm != PBKDF2_ALGORITHM or int(iterations_s) < iterations


# ======================================================================
# JWT
#
# Hand-rolled HS256 rather than a PyJWT dependency: the whole surface we
# need is sign/verify on a compact claims dict, and it keeps the image's
# dependency graph one package smaller. The signature check below is
# constant-time and the expiry is enforced, which is the part that matters.
# ======================================================================

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _sign(signing_input: bytes, secret: str) -> str:
    return _b64url_encode(
        hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    )


def create_access_token(
    subject: str,
    *,
    expires_minutes: Optional[int] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
    secret: Optional[str] = None,
) -> str:
    now = int(time.time())
    ttl = expires_minutes if expires_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    payload: Dict[str, Any] = {"sub": subject, "iat": now, "exp": now + ttl * 60}
    if extra_claims:
        payload.update(extra_claims)

    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    segments.append(_sign(signing_input, secret or settings.SECRET_KEY))
    return ".".join(segments)


def decode_access_token(token: str, *, secret: Optional[str] = None) -> Dict[str, Any]:
    """
    Verify signature and expiry, returning the claims.

    Raises TokenError on anything wrong. Note the algorithm is pinned to
    HS256 from the header check onward -- accepting whatever ``alg`` the
    token asks for is the classic JWT forgery hole ("alg": "none").
    """
    try:
        header_b64, payload_b64, signature = token.split(".")
    except (ValueError, AttributeError):
        raise TokenError("Malformed token")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise TokenError("Malformed token")

    if header.get("alg") != JWT_ALGORITHM:
        raise TokenError("Unsupported token algorithm")

    expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"), secret or settings.SECRET_KEY)
    if not hmac.compare_digest(expected, signature):
        raise TokenError("Bad token signature")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= exp:
        raise TokenError("Token has expired")

    if not payload.get("sub"):
        raise TokenError("Token has no subject")

    return payload
