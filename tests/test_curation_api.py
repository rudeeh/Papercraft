"""Tests for the curation / attestation API routes."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app
from app.db.models import DraftKind, DraftStatus, ExtractionDraft, RoutingDecision


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def no_graph():
    """Curation routes resolve Neo4j lazily; keep the tests off the network."""
    with patch("app.api.curation_routes._graph_repository", return_value=MagicMock()):
        yield


async def _register(ac, email="curator@example.com"):
    response = await ac.post(
        "/api/v1/auth/register",
        json={"email": email, "display_name": "Curator", "password": "a-good-long-password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class TestListDrafts:
    @pytest.mark.asyncio
    async def test_lists_pending_drafts_by_default(self, db_engine, client, make_draft):
        make_draft()
        async with client as ac:
            response = await ac.get("/api/v1/curation/drafts")
        assert response.status_code == 200
        assert len(response.json()["drafts"]) == 1

    @pytest.mark.asyncio
    async def test_filters_by_paper_id(self, db_engine, client, make_draft):
        make_draft(paper_id="paper_1")
        make_draft(paper_id="paper_2")
        async with client as ac:
            response = await ac.get("/api/v1/curation/drafts", params={"paper_id": "paper_2"})
        assert len(response.json()["drafts"]) == 1

    @pytest.mark.asyncio
    async def test_a_typo_in_a_filter_is_an_error_not_an_empty_list(
        self, db_engine, client, make_draft
    ):
        """An empty list would read as 'the queue is clear', the opposite of the truth."""
        make_draft()
        async with client as ac:
            response = await ac.get("/api/v1/curation/drafts", params={"status": "pendign"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_routing_filter(self, db_engine, client):
        async with client as ac:
            response = await ac.get("/api/v1/curation/drafts", params={"routing": "maybe"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_single_draft_includes_its_attestations(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            headers = await _register(ac)
            await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}, headers=headers
            )
            response = await ac.get(f"/api/v1/curation/drafts/{draft.id}")
        assert len(response.json()["attestations"]) == 1

    @pytest.mark.asyncio
    async def test_unknown_draft_is_404(self, db_engine, client):
        async with client as ac:
            response = await ac.get("/api/v1/curation/drafts/does-not-exist")
        assert response.status_code == 404


class TestAttest:
    @pytest.mark.asyncio
    async def test_upvote_moves_the_score(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            headers = await _register(ac)
            response = await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}, headers=headers
            )
        assert response.status_code == 200 and response.json()["score"] == 1

    @pytest.mark.asyncio
    async def test_anonymous_curation_works_in_single_player_mode(
        self, db_engine, client, make_draft
    ):
        draft = make_draft()
        async with client as ac:
            response = await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_vote_is_rejected(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            response = await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 7}
            )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_unknown_draft_is_404(self, db_engine, client):
        async with client as ac:
            response = await ac.post("/api/v1/curation/drafts/nope/attest", json={"vote": 1})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_two_curators_promote_a_draft(self, db_engine, client, make_draft, db_session):
        draft = make_draft()
        async with client as ac:
            first = await _register(ac, "a@example.com")
            second = await _register(ac, "b@example.com")
            await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}, headers=first
            )
            response = await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}, headers=second
            )
        assert response.json()["promoted"] is True
        db_session.expire_all()
        assert db_session.get(ExtractionDraft, draft.id).status == DraftStatus.PROMOTED.value


class TestResolve:
    @pytest.mark.asyncio
    async def test_promote_marks_the_draft_promoted(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            response = await ac.post(f"/api/v1/curation/drafts/{draft.id}/promote", json={})
        assert response.status_code == 200
        assert response.json()["status"] == DraftStatus.PROMOTED.value

    @pytest.mark.asyncio
    async def test_reject_marks_the_draft_rejected(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            response = await ac.post(f"/api/v1/curation/drafts/{draft.id}/reject", json={})
        assert response.json()["status"] == DraftStatus.REJECTED.value

    @pytest.mark.asyncio
    async def test_promoting_a_rejected_draft_is_a_conflict(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            await ac.post(f"/api/v1/curation/drafts/{draft.id}/reject", json={})
            response = await ac.post(f"/api/v1/curation/drafts/{draft.id}/promote", json={})
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_promote_works_without_a_body(self, db_engine, client, make_draft):
        draft = make_draft()
        async with client as ac:
            response = await ac.post(f"/api/v1/curation/drafts/{draft.id}/promote")
        assert response.status_code == 200


class TestStatsAndLeaderboard:
    @pytest.mark.asyncio
    async def test_stats_report_queue_counts(self, db_engine, client, make_draft):
        make_draft()
        make_draft()
        async with client as ac:
            response = await ac.get("/api/v1/curation/stats")
        assert response.json() == {"pending": 2, "promoted": 0, "rejected": 0, "total": 2}

    @pytest.mark.asyncio
    async def test_leaderboard_lists_curators_who_earned_reputation(
        self, db_engine, client, make_draft
    ):
        draft = make_draft()
        async with client as ac:
            headers = await _register(ac, "winner@example.com")
            await ac.post(
                f"/api/v1/curation/drafts/{draft.id}/attest", json={"vote": 1}, headers=headers
            )
            await ac.post(f"/api/v1/curation/drafts/{draft.id}/promote", json={})
            response = await ac.get("/api/v1/curation/leaderboard")
        assert [c["display_name"] for c in response.json()["curators"]] == ["Curator"]
