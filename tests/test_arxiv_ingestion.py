"""
Tests for the arXiv ingestion scaffold.

Covers:
  * ID normalization (every accepted shape, plus rejections).
  * ArxivClient.fetch_metadata parsing against a fixture Atom response.
  * ArxivClient.download_pdf streams to disk and renames atomically.
  * The /api/v1/ingest/arxiv endpoint validates input, dispatches the
    Celery task, and returns the canonical id + task id.
  * The Celery task delegates correctly (download -> pipeline) without
    actually hitting the network or the worker.
"""

from __future__ import annotations

import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# ID normalization
# ---------------------------------------------------------------------------


class TestNormalizeArxivId:
    @pytest.mark.parametrize(
        "raw, expected_id, expected_version",
        [
            ("2401.12345", "2401.12345", None),
            ("2401.12345v2", "2401.12345", 2),
            ("arXiv:2401.12345v3", "2401.12345", 3),
            ("https://arxiv.org/abs/2401.12345", "2401.12345", None),
            ("https://arxiv.org/abs/2401.12345v2", "2401.12345", 2),
            ("https://arxiv.org/pdf/2401.12345v2.pdf", "2401.12345", 2),
            ("http://arxiv.org/pdf/2401.12345", "2401.12345", None),
            ("cs/0701001", "cs/0701001", None),
            ("cs/0701001v1", "cs/0701001", 1),
        ],
    )
    def test_accepts_known_shapes(self, raw, expected_id, expected_version):
        from app.services.arxiv import normalize_arxiv_id

        canonical, version = normalize_arxiv_id(raw)
        assert canonical == expected_id
        assert version == expected_version

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not-an-id",
            "2401.12",          # too few digits after the dot
            "2401.1234567",     # too many digits
            "https://example.com/2401.12345",
            "doi:10.1000/xyz",
        ],
    )
    def test_rejects_invalid_shapes(self, raw):
        from app.services.arxiv import InvalidArxivId, normalize_arxiv_id

        with pytest.raises(InvalidArxivId):
            normalize_arxiv_id(raw)

    def test_none_input_raises(self):
        from app.services.arxiv import InvalidArxivId, normalize_arxiv_id

        with pytest.raises(InvalidArxivId):
            normalize_arxiv_id(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Atom XML parsing
# ---------------------------------------------------------------------------


ATOM_FIXTURE = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:arxiv="http://arxiv.org/schemas/atom"
          xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults xmlns="http://a9.com/-/spec/opensearch/1.1/">1</opensearch:totalResults>
      <entry>
        <id>http://arxiv.org/abs/2401.12345v2</id>
        <updated>2024-02-01T00:00:00Z</updated>
        <published>2024-01-22T00:00:00Z</published>
        <title>Mixtral of Experts: A Sparse Mixture-of-Experts Language Model</title>
        <summary>We introduce Mixtral, a decoder-only model...</summary>
        <author><name>Albert Q. Jiang</name></author>
        <author><name>Sabrina Sablayrolles</name></author>
        <arxiv:primary_category xmlns="http://arxiv.org/schemas/atom" term="cs.CL"/>
        <category term="cs.CL"/>
        <category term="cs.LG"/>
        <link href="http://dx.doi.org/10.48550/arXiv.2401.12345" title="doi" rel="related"/>
        <link href="http://arxiv.org/pdf/2401.12345v2" title="pdf" type="application/pdf" rel="related"/>
      </entry>
    </feed>
    """
).encode("utf-8")


class TestArxivClientFetchMetadata:
    def test_parses_atom_feed_into_metadata(self):
        from app.services.arxiv import ArxivClient

        client = ArxivClient(request_delay_s=0.0)
        with patch.object(ArxivClient, "_get_text", return_value=ATOM_FIXTURE.decode("utf-8")):
            meta = client.fetch_metadata("2401.12345v2")

        assert meta.arxiv_id == "2401.12345"
        assert meta.arxiv_id_versioned == "2401.12345v2"
        assert meta.title.startswith("Mixtral of Experts")
        assert meta.authors == ["Albert Q. Jiang", "Sabrina Sablayrolles"]
        assert meta.primary_category == "cs.CL"
        assert meta.categories == ["cs.CL", "cs.LG"]
        assert meta.doi == "10.48550/arXiv.2401.12345"
        assert meta.published == "2024-01-22T00:00:00Z"
        assert meta.updated == "2024-02-01T00:00:00Z"
        assert meta.pdf_url == "https://arxiv.org/pdf/2401.12345v2.pdf"

    def test_no_entries_raises_not_found(self):
        from app.services.arxiv import ArxivClient, ArxivNotFound

        empty_feed = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        client = ArxivClient(request_delay_s=0.0)
        with patch.object(ArxivClient, "_get_text", return_value=empty_feed):
            with pytest.raises(ArxivNotFound):
                client.fetch_metadata("9999.99999")

    def test_error_entry_raises_not_found(self):
        """arXiv sometimes returns an entry whose title is literally 'Error'."""
        from app.services.arxiv import ArxivClient, ArxivNotFound

        error_feed = (
            '<?xml version="1.0"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><id>http://arxiv.org/abs/</id><title>Error</title><summary/></entry>'
            "</feed>"
        )
        client = ArxivClient(request_delay_s=0.0)
        with patch.object(ArxivClient, "_get_text", return_value=error_feed):
            with pytest.raises(ArxivNotFound):
                client.fetch_metadata("9999.99999")


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------


class TestArxivClientDownloadPdf:
    def test_streams_to_partial_then_renames(self, tmp_path):
        from app.services.arxiv import ArxivClient

        dest = tmp_path / "paper.pdf"
        client = ArxivClient(request_delay_s=0.0)

        # fetch_metadata is called internally; stub it to skip the network.
        fake_meta = MagicMock(
            arxiv_id="2401.12345",
            arxiv_id_versioned="2401.12345v2",
            pdf_url="https://arxiv.org/pdf/2401.12345v2.pdf",
        )
        fake_meta.to_dict = MagicMock(return_value={"arxiv_id": "2401.12345"})
        with patch.object(ArxivClient, "fetch_metadata", return_value=fake_meta):
            # Stub the streaming HTTP request.
            fake_response = MagicMock()
            fake_response.status_code = 200
            fake_response.iter_bytes = MagicMock(
                return_value=iter([b"PDF-1.4 ", b"body-bytes"])
            )
            fake_ctx = MagicMock()
            fake_ctx.__enter__ = MagicMock(return_value=fake_response)
            fake_ctx.__exit__ = MagicMock(return_value=False)

            fake_client = MagicMock()
            fake_client.stream = MagicMock(return_value=fake_ctx)
            fake_client.__enter__ = MagicMock(return_value=fake_client)
            fake_client.__exit__ = MagicMock(return_value=False)

            with patch("app.services.arxiv.httpx.Client", return_value=fake_client):
                meta = client.download_pdf("2401.12345", str(dest))

        assert os.path.exists(dest)
        assert dest.read_bytes() == b"PDF-1.4 body-bytes"
        assert not os.path.exists(str(dest) + ".partial")
        assert meta.arxiv_id == "2401.12345"

    def test_404_raises_not_found(self, tmp_path):
        from app.services.arxiv import ArxivClient, ArxivNotFound

        dest = tmp_path / "paper.pdf"
        client = ArxivClient(request_delay_s=0.0)

        fake_meta = MagicMock(pdf_url="https://arxiv.org/pdf/9999.99999.pdf")
        fake_meta.to_dict = MagicMock(return_value={})
        with patch.object(ArxivClient, "fetch_metadata", return_value=fake_meta):
            fake_response = MagicMock()
            fake_response.status_code = 404
            fake_ctx = MagicMock()
            fake_ctx.__enter__ = MagicMock(return_value=fake_response)
            fake_ctx.__exit__ = MagicMock(return_value=False)
            fake_client = MagicMock()
            fake_client.stream = MagicMock(return_value=fake_ctx)
            fake_client.__enter__ = MagicMock(return_value=fake_client)
            fake_client.__exit__ = MagicMock(return_value=False)
            with patch("app.services.arxiv.httpx.Client", return_value=fake_client):
                with pytest.raises(ArxivNotFound):
                    client.download_pdf("9999.99999", str(dest))
        assert not os.path.exists(dest)


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from app.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestIngestArxivEndpoint:
    @pytest.mark.asyncio
    async def test_rejects_invalid_id_with_422(self, client):
        async with client as ac:
            resp = await ac.post("/api/v1/ingest/arxiv", json={"arxiv_id": "not-an-id"})
        assert resp.status_code == 422
        assert "arxiv" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_dispatches_celery_task_and_returns_canonical_id(self, client):
        with patch("app.api.arxiv_routes.celery_app.send_task") as mock_send:
            mock_send.return_value = MagicMock(id="task-abc-123")
            with patch("app.api.arxiv_routes.celery_app.backend.store_result"):
                async with client as ac:
                    resp = await ac.post(
                        "/api/v1/ingest/arxiv",
                        json={"arxiv_id": "https://arxiv.org/abs/2401.12345v2"},
                    )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["arxiv_id"] == "2401.12345"
        assert body["doc_id"] == "arxiv-2401.12345"
        assert body["task_id"] == "task-abc-123"
        assert "started" in body["message"].lower()
        mock_send.assert_called_once_with(
            "app.worker.tasks.process_arxiv_task",
            args=["https://arxiv.org/abs/2401.12345v2"],
        )

    @pytest.mark.asyncio
    async def test_route_is_registered(self, client):
        """Smoke check: the path exists in the FastAPI app's route table."""
        from app.api.main import app

        paths = [r.path for r in app.routes]
        assert "/api/v1/ingest/arxiv" in paths


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------


class TestProcessArxivTask:
    def test_invalid_id_returns_failed_payload_without_raising(self):
        from app.worker.arxiv_tasks import process_arxiv_task

        # The @celery_app.task decorator wraps the function; the original
        # callable is preserved as `_orig_run`. Because of `bind=True` the
        # task object itself acts as `self`, so we call it directly.
        result = process_arxiv_task._orig_run("garbage-id")

        assert result["status"] == "failed"
        assert "invalid arXiv id" in result["error"]
        assert result["raw_id"] == "garbage-id"

    def test_happy_path_downloads_then_runs_pipeline(self, tmp_path, monkeypatch):
        from app.worker import arxiv_tasks

        # Make the worker write the PDF into tmp_path.
        monkeypatch.setattr(arxiv_tasks.settings, "UPLOAD_DIR", str(tmp_path))

        # Stub self.update_state so we don't need a real Celery backend.
        monkeypatch.setattr(
            arxiv_tasks.process_arxiv_task,
            "update_state",
            lambda *a, **kw: None,
        )

        # Pretend the PDF is already on disk so download is skipped.
        fake_meta = MagicMock(arxiv_id="2401.12345")
        fake_meta.to_dict = MagicMock(return_value={"arxiv_id": "2401.12345"})
        pdf_path = tmp_path / "arxiv-2401.12345.pdf"
        pdf_path.write_bytes(b"fake pdf bytes")

        # Stub the pipeline so we can assert it was invoked with the right args.
        fake_result = MagicMock(
            status="COMPLETED",
            vector_count=10,
            citation_count=2,
            entity_count=3,
            relation_count=1,
            graph_nodes_count=4,
            graph_edges_count=3,
            auto_inserted_count=2,
            queued_for_review_count=1,
            enriched=True,
            steps=[],
        )
        fake_pipeline_instance = MagicMock()
        fake_pipeline_instance.process.return_value = fake_result

        # All the factory helpers should be no-ops for the test.
        monkeypatch.setattr(arxiv_tasks, "_create_neo4j_client", lambda: None)
        monkeypatch.setattr(arxiv_tasks, "_create_vector_repo", lambda: None)
        monkeypatch.setattr(arxiv_tasks, "_create_openalex_client", lambda: None)
        monkeypatch.setattr(arxiv_tasks, "_create_llm_extractor", lambda: None)
        monkeypatch.setattr(arxiv_tasks, "_create_draft_sink", lambda: None)
        monkeypatch.setattr(arxiv_tasks, "_record_job", lambda *a, **k: None)
        monkeypatch.setattr(
            arxiv_tasks,
            "PaperIngestionPipeline",
            MagicMock(return_value=fake_pipeline_instance),
        )
        # And the arxiv client only needs to return metadata (PDF cached).
        fake_client = MagicMock()
        fake_client.fetch_metadata.return_value = fake_meta
        monkeypatch.setattr(arxiv_tasks, "ArxivClient", MagicMock(return_value=fake_client))

        result = arxiv_tasks.process_arxiv_task._orig_run("2401.12345v2")

        assert result["status"] == "completed"
        assert result["arxiv_id"] == "2401.12345"
        assert result["doc_id"] == "arxiv-2401.12345"
        assert result["arxiv"] == {"arxiv_id": "2401.12345"}
        assert result["chunks_count"] == 10
        fake_pipeline_instance.process.assert_called_once_with(
            paper_id="arxiv-2401.12345",
            file_path=str(pdf_path),
        )
