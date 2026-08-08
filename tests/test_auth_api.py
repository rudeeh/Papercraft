"""Tests for the auth routes and the optional-auth dependency."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import AuditLog, User

REGISTRATION = {
    "email": "curator@example.com",
    "display_name": "Curator",
    "password": "a-good-long-password",
}


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def restore_auth_required():
    original = settings.AUTH_REQUIRED
    yield
    settings.AUTH_REQUIRED = original


class TestRegister:
    @pytest.mark.asyncio
    async def test_returns_a_usable_token(self, db_engine, client):
        async with client as ac:
            response = await ac.post("/api/v1/auth/register", json=REGISTRATION)
        assert response.status_code == 201
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == "curator@example.com"
        assert body["access_token"]

    @pytest.mark.asyncio
    async def test_never_echoes_the_password_or_its_hash(self, db_engine, client):
        async with client as ac:
            response = await ac.post("/api/v1/auth/register", json=REGISTRATION)
        assert "password_hash" not in response.json()["user"]
        assert REGISTRATION["password"] not in response.text

    @pytest.mark.asyncio
    async def test_duplicate_email_is_a_conflict(self, db_engine, client):
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
            response = await ac.post("/api/v1/auth/register", json=REGISTRATION)
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_email_is_normalised_to_lowercase(self, db_engine, client, db_session):
        async with client as ac:
            await ac.post(
                "/api/v1/auth/register", json={**REGISTRATION, "email": "MiXeD@Example.COM"}
            )
        assert db_session.query(User).one().email == "mixed@example.com"

    @pytest.mark.asyncio
    async def test_short_password_rejected(self, db_engine, client):
        async with client as ac:
            response = await ac.post(
                "/api/v1/auth/register", json={**REGISTRATION, "password": "short"}
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_email_rejected(self, db_engine, client):
        async with client as ac:
            response = await ac.post(
                "/api/v1/auth/register", json={**REGISTRATION, "email": "not-an-email"}
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reserved_anonymous_address_rejected(self, db_engine, client):
        async with client as ac:
            response = await ac.post(
                "/api/v1/auth/register",
                json={**REGISTRATION, "email": "anonymous@papercraft.local"},
            )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_registration_is_audited(self, db_engine, client, db_session):
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
        assert db_session.query(AuditLog).filter(AuditLog.action == "user.register").count() == 1


class TestLogin:
    @pytest.mark.asyncio
    async def test_correct_credentials_return_a_token(self, db_engine, client):
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
            response = await ac.post(
                "/api/v1/auth/login",
                json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
            )
        assert response.status_code == 200 and response.json()["access_token"]

    @pytest.mark.asyncio
    async def test_wrong_password_is_401(self, db_engine, client):
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
            response = await ac.post(
                "/api/v1/auth/login", json={"email": REGISTRATION["email"], "password": "wrong"}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_user_gives_the_same_error_as_a_wrong_password(self, db_engine, client):
        """Different messages here would turn login into an account-enumeration oracle."""
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
            wrong_password = await ac.post(
                "/api/v1/auth/login", json={"email": REGISTRATION["email"], "password": "wrong"}
            )
            unknown_user = await ac.post(
                "/api/v1/auth/login", json={"email": "ghost@example.com", "password": "wrong"}
            )
        assert wrong_password.status_code == unknown_user.status_code == 401
        assert wrong_password.json()["detail"] == unknown_user.json()["detail"]

    @pytest.mark.asyncio
    async def test_disabled_account_is_403(self, db_engine, client, db_session):
        async with client as ac:
            await ac.post("/api/v1/auth/register", json=REGISTRATION)
            db_session.query(User).filter(User.email == REGISTRATION["email"]).one().is_active = False
            db_session.commit()
            response = await ac.post(
                "/api/v1/auth/login",
                json={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
            )
        assert response.status_code == 403


class TestMe:
    @pytest.mark.asyncio
    async def test_bearer_token_identifies_the_user(self, db_engine, client):
        async with client as ac:
            token = (await ac.post("/api/v1/auth/register", json=REGISTRATION)).json()["access_token"]
            response = await ac.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.json()["email"] == "curator@example.com"

    @pytest.mark.asyncio
    async def test_no_token_falls_back_to_anonymous(self, db_engine, client):
        async with client as ac:
            response = await ac.get("/api/v1/auth/me")
        assert response.status_code == 200
        assert response.json()["email"] == "anonymous@papercraft.local"

    @pytest.mark.asyncio
    async def test_anonymous_user_is_created_only_once(self, db_engine, client, db_session):
        async with client as ac:
            await ac.get("/api/v1/auth/me")
            await ac.get("/api/v1/auth/me")
        assert db_session.query(User).filter(User.email == "anonymous@papercraft.local").count() == 1

    @pytest.mark.asyncio
    async def test_a_bad_token_is_401_even_in_single_player_mode(self, db_engine, client):
        """A *wrong* credential is an error; only a *missing* one falls back."""
        async with client as ac:
            response = await ac.get(
                "/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.here"}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_non_bearer_scheme_rejected(self, db_engine, client):
        async with client as ac:
            response = await ac.get("/api/v1/auth/me", headers={"Authorization": "Basic abc123"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_token_for_a_deleted_user_is_401(self, db_engine, client):
        token = create_access_token("no-such-user-id")
        async with client as ac:
            response = await ac.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_auth_required_disables_the_anonymous_fallback(
        self, db_engine, client, restore_auth_required
    ):
        settings.AUTH_REQUIRED = True
        async with client as ac:
            response = await ac.get("/api/v1/auth/me")
        assert response.status_code == 401
