from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport
from app.api.main import app


def _can_import(module_name: str) -> bool:
    """True if `import module_name` succeeds. Used by skipif decorators
    so tests that need optional deps skip gracefully in CI."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False

@pytest.mark.asyncio
async def test_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestLlmStatus:
    @pytest.mark.asyncio
    async def test_reports_configured_when_server_key_present(self):
        with patch("app.services.llm.llm_client") as mock_client:
            mock_client.has_server_key = True
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/llm-status")
        assert response.status_code == 200
        assert response.json() == {"server_key_configured": True}

    @pytest.mark.asyncio
    async def test_reports_not_configured_when_no_server_key(self):
        with patch("app.services.llm.llm_client") as mock_client:
            mock_client.has_server_key = False
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/llm-status")
        assert response.status_code == 200
        assert response.json() == {"server_key_configured": False}


class TestChatEndpoint:
    @staticmethod
    def _patch_chat_deps():
        mock_model = MagicMock()
        mock_model.encode.return_value.tolist.return_value = [0.1] * 384

        mock_hit = MagicMock()
        # Matches what VectorRepository actually stores: chunks are keyed
        # by "paper_id", with no separate doc_id/filename field.
        mock_hit.payload = {"paper_id": "d1", "page": 1, "text": "some text"}

        return (
            patch("app.services.embeddings.get_model", return_value=mock_model),
            patch("app.services.vector_store.search_vectors", return_value=[mock_hit]),
        )

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _can_import("sentence_transformers"),
        reason="app.services.embeddings imports sentence_transformers at module load; CI skips it to save ~2 GB torch download",
    )
    async def test_passes_request_api_key_to_llm_client(self):
        get_model_patch, search_patch = self._patch_chat_deps()
        with get_model_patch, search_patch, \
             patch("app.services.llm.llm_client") as mock_llm:
            mock_llm.generate_response.return_value = "an answer"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat",
                    json={"query": "hello", "api_key": "sk-or-user-supplied"},
                )

        assert response.status_code == 200
        mock_llm.generate_response.assert_called_once()
        assert mock_llm.generate_response.call_args.kwargs["api_key"] == "sk-or-user-supplied"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _can_import("sentence_transformers"),
        reason="app.services.embeddings imports sentence_transformers at module load; CI skips it to save ~2 GB torch download",
    )
    async def test_no_api_key_available_returns_401(self):
        from app.services.llm import LLMNotConfiguredError

        get_model_patch, search_patch = self._patch_chat_deps()
        with get_model_patch, search_patch, \
             patch("app.services.llm.llm_client") as mock_llm:
            mock_llm.generate_response.side_effect = LLMNotConfiguredError("no key")
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/api/v1/chat", json={"query": "hello"})

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_blank_query_returns_422(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/v1/chat", json={"query": "   "})

        assert response.status_code == 422


class TestStatusEndpoint:
    """
    Celery reports PENDING both for queued-but-not-started work and for an
    ID that was never issued, so /status leans on the backend having a
    record (written at enqueue time) to tell a typo from real work.
    """

    @staticmethod
    def _patch_celery(exists, side_effect=None, status="PENDING"):
        """
        Patch the ``celery_app`` name as imported into routes -- Celery's
        real ``backend`` is a read-only property and can't be patched
        directly.
        """
        mock_celery = MagicMock()
        mock_celery.backend.get_key_for_task.return_value = "celery-task-meta-x"
        if side_effect is not None:
            mock_celery.backend.client.exists.side_effect = side_effect
        else:
            mock_celery.backend.client.exists.return_value = exists

        mock_result = MagicMock()
        mock_result.status = status
        mock_result.ready.return_value = False
        mock_celery.AsyncResult.return_value = mock_result

        return patch("app.api.routes.celery_app", mock_celery)

    @pytest.mark.asyncio
    async def test_unknown_task_id_returns_404(self):
        with self._patch_celery(exists=0):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/status/not-a-real-task-id")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_known_task_id_returns_status(self):
        with self._patch_celery(exists=1):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/status/real-task-id")

        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_backend_error_fails_open_rather_than_404ing(self):
        with self._patch_celery(exists=None, side_effect=RuntimeError("no redis")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get("/api/v1/status/some-task-id")

        assert response.status_code == 200
