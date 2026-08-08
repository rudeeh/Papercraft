"""Tests for app/core/security.py -- password hashing and JWT."""

import base64
import json
import time

import pytest

from app.core.security import (
    JWT_ALGORITHM,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)


class TestPasswordHashing:
    def test_roundtrip(self):
        stored = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", stored)

    def test_wrong_password_rejected(self):
        stored = hash_password("hunter2")
        assert not verify_password("hunter3", stored)

    def test_salted__same_password_hashes_differently(self):
        assert hash_password("same") != hash_password("same")

    def test_empty_password_rejected(self):
        with pytest.raises(ValueError):
            hash_password("")

    @pytest.mark.parametrize(
        "corrupt",
        ["", "not-a-hash", "pbkdf2_sha256$notanint$aa$bb", "bcrypt$1$aa$bb", "a$b$c"],
    )
    def test_malformed_hash_is_false_not_an_exception(self, corrupt):
        # A corrupt row must be a failed login, never a 500.
        assert verify_password("anything", corrupt) is False

    def test_anonymous_sentinel_hash_can_never_match(self):
        # app/api/deps.py stores "!" for the anonymous user precisely so no
        # password can authenticate as it.
        assert verify_password("", "!") is False
        assert verify_password("!", "!") is False

    def test_needs_rehash_detects_weaker_cost(self):
        weak = hash_password("pw", iterations=1000)
        assert needs_rehash(weak)
        assert not needs_rehash(hash_password("pw"))


class TestAccessTokens:
    def test_roundtrip_carries_subject_and_extra_claims(self):
        token = create_access_token("user-123", extra_claims={"email": "a@b.c"})
        claims = decode_access_token(token)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "a@b.c"

    def test_expired_token_rejected(self):
        token = create_access_token("user-123", expires_minutes=-1)
        with pytest.raises(TokenError, match="expired"):
            decode_access_token(token)

    def test_tampered_payload_rejected(self):
        token = create_access_token("user-123")
        header, payload, signature = token.split(".")
        forged_payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "admin", "exp": int(time.time()) + 600}).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(TokenError, match="signature"):
            decode_access_token(f"{header}.{forged_payload}.{signature}")

    def test_wrong_secret_rejected(self):
        token = create_access_token("user-123", secret="secret-a")
        with pytest.raises(TokenError, match="signature"):
            decode_access_token(token, secret="secret-b")

    def test_alg_none_forgery_rejected(self):
        """The classic JWT hole: a token asking to be verified with no algorithm."""
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "none", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "admin", "exp": int(time.time()) + 600}).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(TokenError, match="algorithm"):
            decode_access_token(f"{header}.{payload}.")

    @pytest.mark.parametrize("bad", ["", "abc", "a.b", "a.b.c.d", "...."])
    def test_malformed_token_rejected(self, bad):
        with pytest.raises(TokenError):
            decode_access_token(bad)

    def test_header_declares_hs256(self):
        header_b64 = create_access_token("u").split(".")[0]
        padding = "=" * (-len(header_b64) % 4)
        assert json.loads(base64.urlsafe_b64decode(header_b64 + padding))["alg"] == JWT_ALGORITHM
