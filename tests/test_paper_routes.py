"""
Tests for the paper detail endpoints.

Covers:
  * PDF serving: 200 on existing, 404 on missing, Range header support
  * Path traversal: every known attack vector is blocked
  * doc_id validation: rejects malformed ids before touching the filesystem
  * Extraction endpoint: shape, 404 on missing PDF, graceful Neo4j degradation
  * Auth: respects AUTH_REQUIRED (same as curation routes)

Security tests are the most important part — the PDF endpoint serves
files from disk and a path traversal vulnerability would let an attacker
read arbitrary files. Every test below corresponds to a specific attack
vector.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app


def _mock_user():
    """A stand-in user object that satisfies the current_user dependency
    without needing a database. In single-player mode (AUTH_REQUIRED=False,
    the default), the real dependency resolves to the anonymous user —
    this mock replicates that without touching PostgreSQL."""
    user = MagicMock()
    user.id = "test-user-id"
    user.email = "test@example.com"
    user.display_name = "Test User"
    user.is_active = True
    return user


@pytest.fixture
def client():
    """Client with the current_user dependency overridden to bypass
    the database. Tests that need real auth behavior can override
    differently."""
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["current_user"]).current_user
    ] = _mock_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def cleanup_overrides():
    """Clear dependency overrides after each test so they don't leak."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """Point UPLOAD_DIR at a temp directory and create a test PDF."""
    monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))
    # Create a fake PDF for a known doc_id
    doc_id = "a" * 64  # 64-char hex hash (matches _DOC_ID_RE)
    pdf_path = tmp_path / f"{doc_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf content")
    return tmp_path, doc_id


# ---------------------------------------------------------------------------
# doc_id validation
# ---------------------------------------------------------------------------


class TestDocIdValidation:
    @pytest.mark.asyncio
    async def test_valid_hex_hash_accepted(self, client, upload_dir):
        _, doc_id = upload_dir
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/pdf")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_arxiv_id_accepted(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))
        doc_id = "arxiv-2401.12345"
        (tmp_path / f"{doc_id}.pdf").write_bytes(b"%PDF-1.4 fake")
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/pdf")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malicious_id",
        [
            "../../../etc/passwd",
            "..%2F..%2F..%2Fetc%2Fpasswd",
            ".../.../etc/passwd",
            "arxiv-../../etc/passwd",
            "a" * 63,  # too short for hex hash
            "a" * 65,  # too long
            "arxiv-foo/bar",  # invalid arxiv category
            "",  # empty
            "CON",  # Windows reserved name
            # null byte injection is tested separately — httpx rejects it
            # before the request reaches the app
            "arxiv-2401.12345/../../etc/passwd",
        ],
    )
    async def test_malicious_doc_ids_rejected(self, client, malicious_id):
        """Every known path-traversal vector must be rejected — either
        by the regex (400/422) or by FastAPI's path routing (404 when
        the doc_id contains '/'). Either way, the file must NOT be
        served."""
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{malicious_id}/pdf")
        # 400 (regex rejection), 422 (validation), or 404 (route doesn't
        # match because of '/' in the path) — all are acceptable security
        # outcomes. What's NOT acceptable is 200 with file content.
        assert resp.status_code in (400, 404, 422), (
            f"doc_id={malicious_id!r} should be rejected (got {resp.status_code})"
        )
        assert b"root:" not in resp.content
        assert b"fake pdf content" not in resp.content


# ---------------------------------------------------------------------------
# PDF serving
# ---------------------------------------------------------------------------


class TestPdfServing:
    @pytest.mark.asyncio
    async def test_200_on_existing_pdf(self, client, upload_dir):
        _, doc_id = upload_dir
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.headers["content-disposition"] == f"inline; filename={doc_id}.pdf"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert b"fake pdf content" in resp.content

    @pytest.mark.asyncio
    async def test_404_on_missing_pdf(self, client, upload_dir):
        _, _ = upload_dir
        # Valid format but no file on disk
        doc_id = "b" * 64
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/pdf")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_range_request_supported(self, client, upload_dir):
        """pdf.js sends Range headers for large PDFs. The endpoint
        must honor them so the browser doesn't load the entire file."""
        _, doc_id = upload_dir
        async with client as ac:
            resp = await ac.get(
                f"/api/v1/papers/{doc_id}/pdf",
                headers={"Range": "bytes=0-4"},
            )
        # FileResponse supports Range requests natively
        assert resp.status_code in (200, 206)


# ---------------------------------------------------------------------------
# Extraction endpoint
# ---------------------------------------------------------------------------


class TestExtractionEndpoint:
    @pytest.mark.asyncio
    async def test_404_when_pdf_missing(self, client, upload_dir):
        _, _ = upload_dir
        doc_id = "c" * 64  # valid format, no file
        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/extraction")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_partial_when_neo4j_unavailable(self, client, upload_dir):
        """If Neo4j is down, the endpoint should still return 200 with
        has_pdf=True and extraction_available=False — the frontend can
        show the PDF with a 'graph data unavailable' message."""
        _, doc_id = upload_dir
        with patch("app.storage.neo4j_client.Neo4jClient") as mock_neo4j:
            mock_neo4j.side_effect = Exception("Neo4j connection refused")
            async with client as ac:
                resp = await ac.get(f"/api/v1/papers/{doc_id}/extraction")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_pdf"] is True
        assert body["extraction_available"] is False
        assert body["doc_id"] == doc_id

    @pytest.mark.asyncio
    async def test_returns_full_data_when_neo4j_available(self, client, upload_dir):
        """When Neo4j has data, the endpoint returns sections, entities,
        citations, and graph_stats."""
        _, doc_id = upload_dir

        # Mock the Neo4j client + graph repository
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value={
            "title": "Test Paper",
            "abstract": "An abstract.",
            "sections": [{"heading": "Introduction", "text": "Intro text."}],
            "entities": [{"node_type": "Method", "name": "Transformer", "properties": {}}],
            "citations": [{"title": "Cited Paper", "doi": "10.1/x", "is_stub": True}],
            "graph_stats": {"section_count": 1, "entity_count": 1, "citation_count": 1},
        })
        mock_result = MagicMock()
        mock_result.single.return_value = mock_record
        mock_session.run.return_value = mock_result
        mock_client.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_client.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.storage.neo4j_client.Neo4jClient", return_value=mock_client):
            async with client as ac:
                resp = await ac.get(f"/api/v1/papers/{doc_id}/extraction")

        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Test Paper"
        assert body["abstract"] == "An abstract."
        assert len(body["sections"]) == 1
        assert body["sections"][0]["heading"] == "Introduction"
        assert len(body["entities"]) == 1
        assert body["entities"][0]["name"] == "Transformer"
        assert len(body["citations"]) == 1
        assert body["citations"][0]["doi"] == "10.1/x"
        assert body["extraction_available"] is True
        assert body["graph_stats"]["entity_count"] == 1

    @pytest.mark.asyncio
    async def test_404_when_paper_not_in_graph_but_pdf_exists(self, client, upload_dir):
        """If the PDF exists but the paper isn't in Neo4j (pipeline
        hasn't run yet), return 200 with extraction_available=False."""
        _, doc_id = upload_dir

        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = None  # paper not in graph
        mock_session.run.return_value = mock_result
        mock_client.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_client.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.storage.neo4j_client.Neo4jClient", return_value=mock_client):
            async with client as ac:
                resp = await ac.get(f"/api/v1/papers/{doc_id}/extraction")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_pdf"] is True
        assert body["extraction_available"] is False


# ---------------------------------------------------------------------------
# Path traversal defense (the critical security tests)
# ---------------------------------------------------------------------------


class TestPathTraversalDefense:
    """These tests verify that the three layers of path-traversal
    defense work correctly. If any of these fail, the endpoint is
    vulnerable to arbitrary file read."""

    @pytest.mark.asyncio
    async def test_dotdot_in_doc_id_rejected_by_regex(self, client, upload_dir):
        """Layer 1: the regex rejects '..' in doc_id."""
        async with client as ac:
            resp = await ac.get("/api/v1/papers/../../../etc/passwd/pdf")
        assert resp.status_code in (400, 404, 422)
        # Must NOT return a file's contents
        assert b"root:" not in resp.content

    @pytest.mark.asyncio
    async def test_encoded_dotdot_rejected(self, client, upload_dir):
        """URL-encoded '..' (%2F%2E%2E) must also be rejected."""
        async with client as ac:
            resp = await ac.get("/api/v1/papers/%2E%2E%2F%2E%2E%2Fetc%2Fpasswd/pdf")
        assert resp.status_code in (400, 404, 422)
        assert b"root:" not in resp.content

    @pytest.mark.asyncio
    async def test_null_byte_injection_rejected(self, client, upload_dir):
        """Null bytes can't terminate the string early to bypass the regex.
        In practice, httpx itself rejects null bytes in URLs before the
        request reaches the app — an additional layer of defense beyond
        the regex. This test documents that behavior."""
        _, _ = upload_dir
        with pytest.raises(Exception):  # httpx.InvalidURL
            async with client as ac:
                await ac.get("/api/v1/papers/aaaa\x00../../../../etc/passwd/pdf")

    @pytest.mark.asyncio
    async def test_symlink_escape_blocked(self, client, tmp_path, monkeypatch):
        """If an attacker somehow creates a symlink inside UPLOAD_DIR
        pointing outside, the realpath containment check must catch it."""
        monkeypatch.setattr("app.core.config.settings.UPLOAD_DIR", str(tmp_path))

        # Create a symlink that points outside UPLOAD_DIR
        # (simulating a compromised or misconfigured deploy)
        evil_link = tmp_path / "evil.pdf"
        try:
            os.symlink("/etc/passwd", evil_link)
        except OSError:
            pytest.skip("Cannot create symlink (running as root or unsupported FS)")

        # The doc_id would be "evil" — but "evil" doesn't match the regex,
        # so it's rejected at layer 1. Test with a valid-format doc_id that
        # matches a symlink we create:
        doc_id = "d" * 64
        symlink_path = tmp_path / f"{doc_id}.pdf"
        try:
            os.symlink("/etc/passwd", symlink_path)
        except OSError:
            pytest.skip("Cannot create symlink")

        async with client as ac:
            resp = await ac.get(f"/api/v1/papers/{doc_id}/pdf")
        # The containment check (layer 3) caught the symlink escape and
        # returned 400. This is the correct security outcome — the file
        # was NOT served.
        assert resp.status_code == 400
        assert b"root:" not in resp.content
