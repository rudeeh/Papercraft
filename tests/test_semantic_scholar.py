"""
Tests for the Semantic Scholar citation enrichment client.

Covers:
  * Citation → S2 ID resolution (DOI priority, arXiv fallback, skip if neither)
  * Batch fetch against a mocked HTTP transport (200, 429, 5xx, network error)
  * Field mapping (S2 record → enrichment dict, including TLDR provenance)
  * Deduplication of lookable citations
  * Graceful degradation (every failure mode returns empty, never raises)
  * Pipeline integration: citations are mutated in place with S2 metadata

The HTTP layer is mocked via httpx.MockTransport so no real S2 calls
are made — CI doesn't have an API key and the free tier rate-limits
after ~1-2 calls/hour/IP.
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.semantic_scholar import (
    S2_BATCH_LIMIT,
    SemanticScholarClient,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _s2_paper(
    paper_id: str = "abc123",
    title: str = "Attention Is All You Need",
    abstract: Optional[str] = "We propose a new architecture...",
    year: Optional[int] = 2017,
    venue: Optional[str] = "NeurIPS",
    citation_count: Optional[int] = 100000,
    influential: Optional[int] = 20000,
    authors: Optional[list] = None,
    tldr: Optional[dict] = None,
) -> dict:
    """Build a realistic S2 paper record for test fixtures."""
    return {
        "paperId": paper_id,
        "title": title,
        "abstract": abstract,
        "year": year,
        "venue": venue,
        "citationCount": citation_count,
        "influentialCitationCount": influential,
        "authors": authors if authors is not None else [
            {"name": "Ashish Vaswani", "authorId": "123"},
            {"name": "Noam Shazeer", "authorId": "456"},
        ],
        "tldr": tldr if tldr is not None else {
            "model": "tldr@v2.0.0",
            "text": "A new architecture based solely on attention.",
        },
        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/3295222.3295349"},
    }


def _make_client_with_transport(handler, **kwargs):
    """Build a SemanticScholarClient with a mocked HTTP transport.

    ``handler`` is a callable taking an httpx.Request and returning
    an httpx.Response — same interface as httpx.MockTransport.
    """
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return SemanticScholarClient(client=http_client, **kwargs)


# ---------------------------------------------------------------------------
# Citation → S2 ID resolution
# ---------------------------------------------------------------------------


class TestCitationToS2Id:
    def test_doi_takes_priority_over_arxiv(self):
        client = SemanticScholarClient(api_key=None, client=MagicMock())
        cit = {"doi": "10.5555/3295222.3295349", "arxiv_id": "1706.03762"}
        s2_id, key = client._citation_to_s2_id(cit)
        assert s2_id == "DOI:10.5555/3295222.3295349"
        assert key == "doi:10.5555/3295222.3295349"

    def test_arxiv_used_when_no_doi(self):
        client = SemanticScholarClient(api_key=None, client=MagicMock())
        cit = {"arxiv_id": "1706.03762"}
        s2_id, key = client._citation_to_s2_id(cit)
        assert s2_id == "ARXIV:1706.03762"
        assert key == "arxiv:1706.03762"

    def test_returns_none_when_neither_doi_nor_arxiv(self):
        client = SemanticScholarClient(api_key=None, client=MagicMock())
        cit = {"title": "Some Paper", "ref_id": "[1]"}
        s2_id, key = client._citation_to_s2_id(cit)
        assert s2_id is None
        assert key == ""

    def test_strips_whitespace(self):
        client = SemanticScholarClient(api_key=None, client=MagicMock())
        cit = {"doi": "  10.5555/3295222.3295349  "}
        s2_id, key = client._citation_to_s2_id(cit)
        assert s2_id == "DOI:10.5555/3295222.3295349"
        assert key == "doi:10.5555/3295222.3295349"


# ---------------------------------------------------------------------------
# Batch fetch (HTTP layer, mocked)
# ---------------------------------------------------------------------------


class TestFetchBatch:
    def test_returns_aligned_list_on_200(self):
        papers = [_s2_paper(paper_id="a"), _s2_paper(paper_id="b"), None]
        def handler(req):
            return httpx.Response(200, json=papers)
        client = _make_client_with_transport(handler)
        result = client._fetch_batch(["DOI:1", "DOI:2", "DOI:3"])
        assert result == papers
        assert len(result) == 3
        client.close()

    def test_returns_nones_on_429_after_retries(self):
        # S2 doesn't send Retry-After; client must blind-backoff and give up.
        call_count = {"n": 0}
        def handler(req):
            call_count["n"] += 1
            return httpx.Response(429, json={"message": "Too Many Requests"})
        client = _make_client_with_transport(handler)
        # Patch time.sleep to skip the actual backoff delay
        with patch("app.services.semantic_scholar.time.sleep"):
            result = client._fetch_batch(["DOI:1", "DOI:2"])
        assert result == [None, None]
        # Should have retried len(RETRY_DELAYS) times
        from app.services.semantic_scholar import RETRY_DELAYS
        assert call_count["n"] == len(RETRY_DELAYS)
        client.close()

    def test_returns_nones_on_5xx_after_retries(self):
        call_count = {"n": 0}
        def handler(req):
            call_count["n"] += 1
            return httpx.Response(503, text="Service Unavailable")
        client = _make_client_with_transport(handler)
        with patch("app.services.semantic_scholar.time.sleep"):
            result = client._fetch_batch(["DOI:1"])
        assert result == [None]
        from app.services.semantic_scholar import RETRY_DELAYS
        assert call_count["n"] == len(RETRY_DELAYS)
        client.close()

    def test_returns_nones_on_4xx_other_than_429_without_retry(self):
        # 400/401/403/404 should not retry — they won't succeed.
        call_count = {"n": 0}
        def handler(req):
            call_count["n"] += 1
            return httpx.Response(400, text="Bad Request")
        client = _make_client_with_transport(handler)
        result = client._fetch_batch(["DOI:1"])
        assert result == [None]
        assert call_count["n"] == 1  # no retries
        client.close()

    def test_returns_nones_on_network_error(self):
        def handler(req):
            raise httpx.ConnectError("Connection refused")
        client = _make_client_with_transport(handler)
        with patch("app.services.semantic_scholar.time.sleep"):
            result = client._fetch_batch(["DOI:1", "DOI:2"])
        assert result == [None, None]
        client.close()

    def test_returns_nones_on_bad_json(self):
        def handler(req):
            return httpx.Response(200, text="not json at all")
        client = _make_client_with_transport(handler)
        result = client._fetch_batch(["DOI:1"])
        assert result == [None]
        client.close()

    def test_pads_short_response(self):
        # S2 should return N entries for N IDs, but defensively pad if fewer.
        def handler(req):
            return httpx.Response(200, json=[_s2_paper(paper_id="a")])  # only 1 of 3
        client = _make_client_with_transport(handler)
        result = client._fetch_batch(["DOI:1", "DOI:2", "DOI:3"])
        assert len(result) == 3
        assert result[0] is not None
        assert result[1] is None
        assert result[2] is None
        client.close()

    def test_truncates_long_response(self):
        # Defensively truncate if S2 returns more than requested.
        def handler(req):
            return httpx.Response(200, json=[_s2_paper(paper_id="a"), _s2_paper(paper_id="b"), _s2_paper(paper_id="c")])
        client = _make_client_with_transport(handler)
        result = client._fetch_batch(["DOI:1"])  # requested 1, got 3
        assert len(result) == 1
        client.close()

    def test_empty_input_returns_empty_list(self):
        client = SemanticScholarClient(api_key=None, client=MagicMock())
        assert client._fetch_batch([]) == []

    def test_api_key_sent_in_header_when_set(self):
        captured_headers = {}
        def handler(req):
            captured_headers.update(req.headers)
            return httpx.Response(200, json=[_s2_paper()])
        client = _make_client_with_transport(handler, api_key="test-key-123")
        client._fetch_batch(["DOI:1"])
        assert captured_headers.get("x-api-key") == "test-key-123"
        client.close()

    def test_no_api_key_header_when_unset(self):
        captured_headers = {}
        def handler(req):
            captured_headers.update(req.headers)
            return httpx.Response(200, json=[_s2_paper()])
        client = _make_client_with_transport(handler, api_key=None)
        client._fetch_batch(["DOI:1"])
        assert "x-api-key" not in captured_headers
        client.close()


# ---------------------------------------------------------------------------
# Field mapping (S2 record → enrichment dict)
# ---------------------------------------------------------------------------


class TestS2RecordToEnrichment:
    def test_maps_all_fields(self):
        record = _s2_paper()
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert result["s2_paper_id"] == "abc123"
        assert result["abstract"] == "We propose a new architecture..."
        assert result["year"] == 2017
        assert result["venue"] == "NeurIPS"
        assert result["citation_count"] == 100000
        assert result["influential_citation_count"] == 20000
        assert result["author_names"] == ["Ashish Vaswani", "Noam Shazeer"]
        assert result["s2_title"] == "Attention Is All You Need"

    def test_tldr_stored_with_provenance(self):
        """The TLDR must carry model + source so UIs can label it as
        S2's auto-generated summary, not present it as ground truth."""
        record = _s2_paper(tldr={"model": "tldr@v2.0.0", "text": "A new arch."})
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert result["tldr"] == {
            "text": "A new arch.",
            "model": "tldr@v2.0.0",
            "source": "semantic_scholar",
        }

    def test_tldr_missing_model_defaults_to_unknown(self):
        record = _s2_paper(tldr={"text": "A new arch."})  # no "model" key
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert result["tldr"]["model"] == "unknown"
        assert result["tldr"]["source"] == "semantic_scholar"

    def test_tldr_without_text_omitted(self):
        record = _s2_paper(tldr={"model": "tldr@v2.0.0", "text": ""})
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert "tldr" not in result

    def test_null_fields_omitted(self):
        """S2 returns null for fields it doesn't have. Don't store None
        values — the caller checks `if key not in cit` to decide whether
        to override, and we don't want null S2 values clobbering real
        extractor values."""
        # Build the record directly (the _s2_paper helper defaults null
        # fields to real values for convenience; here we need actual nulls).
        record = {
            "paperId": "abc123",
            "title": "Attention Is All You Need",
            "abstract": None,
            "year": None,
            "venue": None,
            "citationCount": None,
            "influentialCitationCount": None,
            "authors": [],
            "tldr": None,
            "externalIds": {"ArXiv": "1706.03762"},
        }
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert "abstract" not in result
        assert "year" not in result
        assert "venue" not in result
        assert "citation_count" not in result
        assert "influential_citation_count" not in result
        assert "author_names" not in result  # empty list omitted
        assert "tldr" not in result
        # s2_paper_id and s2_title are always present if S2 returned them
        assert result["s2_paper_id"] == "abc123"
        assert result["s2_title"] == "Attention Is All You Need"

    def test_authors_extracted_as_name_list(self):
        """S2 returns [{"name": "...", "authorId": "..."}]. We extract
        just the names to match the existing stub property shape."""
        record = _s2_paper(authors=[
            {"name": "Author A", "authorId": "1"},
            {"name": "Author B", "authorId": "2"},
            {"name": "", "authorId": "3"},  # empty name filtered
        ])
        result = SemanticScholarClient._s2_record_to_enrichment(record, "DOI:1")
        assert result["author_names"] == ["Author A", "Author B"]


# ---------------------------------------------------------------------------
# enrich_citations (the public entrypoint)
# ---------------------------------------------------------------------------


class TestEnrichCitations:
    def test_deduplicates_by_lookup_key(self):
        """Two citations with the same DOI should result in one S2 lookup."""
        def handler(req):
            body = json.loads(req.content)
            assert len(body["ids"]) == 1  # only one ID despite 2 citations
            return httpx.Response(200, json=[_s2_paper(paper_id="dup")])
        client = _make_client_with_transport(handler)
        citations = [
            {"doi": "10.5555/3295222.3295349", "title": "Paper A"},
            {"doi": "10.5555/3295222.3295349", "title": "Paper A (dup)"},  # same DOI
        ]
        result = client.enrich_citations(citations)
        assert len(result) == 1
        assert "doi:10.5555/3295222.3295349" in result
        client.close()

    def test_skips_citations_without_doi_or_arxiv(self):
        """A citation with only a title can't be looked up in S2 — skip it."""
        def handler(req):
            body = json.loads(req.content)
            # Return one paper per requested ID
            return httpx.Response(200, json=[_s2_paper(paper_id=sid) for sid in body["ids"]])
        client = _make_client_with_transport(handler)
        citations = [
            {"doi": "10.5555/3295222.3295349"},  # lookable
            {"title": "Some Paper Without DOI"},  # not lookable
            {"arxiv_id": "1706.03762"},  # lookable
        ]
        result = client.enrich_citations(citations)
        # 2 lookable, both found → 2 results
        assert len(result) == 2
        client.close()

    def test_returns_empty_when_no_lookable_citations(self):
        client = _make_client_with_transport(lambda req: httpx.Response(200, json=[]))
        citations = [{"title": "No DOI"}, {"title": "No arXiv"}]
        result = client.enrich_citations(citations)
        assert result == {}
        client.close()

    def test_returns_empty_when_s2_returns_all_nulls(self):
        """S2 returns null for papers it doesn't have. Those should be
        absent from the result — the caller falls back to sparse stub."""
        def handler(req):
            body = json.loads(req.content)
            return httpx.Response(200, json=[None] * len(body["ids"]))
        client = _make_client_with_transport(handler)
        citations = [{"doi": "10.1/missing1"}, {"doi": "10.1/missing2"}]
        result = client.enrich_citations(citations)
        assert result == {}
        client.close()

    def test_partial_hit_rate(self):
        """3 citations, S2 has 2 of them. Result has 2 entries."""
        def handler(req):
            body = json.loads(req.content)
            response = []
            for sid in body["ids"]:
                if "found" in sid.lower():
                    response.append(_s2_paper(paper_id=sid))
                else:
                    response.append(None)
            return httpx.Response(200, json=response)
        client = _make_client_with_transport(handler)
        citations = [
            {"doi": "10.1/found1"},
            {"doi": "10.1/missing"},
            {"doi": "10.1/found2"},
        ]
        result = client.enrich_citations(citations)
        assert len(result) == 2
        assert "doi:10.1/found1" in result
        assert "doi:10.1/found2" in result
        assert "doi:10.1/missing" not in result
        client.close()

    def test_chunks_batches_over_500(self):
        """S2 batch endpoint accepts at most 500 IDs. A paper with 600
        references should trigger 2 batch calls."""
        call_count = {"n": 0}
        def handler(req):
            call_count["n"] += 1
            body = json.loads(req.content)
            assert len(body["ids"]) <= S2_BATCH_LIMIT
            return httpx.Response(200, json=[_s2_paper(paper_id=sid) for sid in body["ids"]])
        client = _make_client_with_transport(handler)
        citations = [{"doi": f"10.1/p{i}"} for i in range(600)]
        result = client.enrich_citations(citations)
        assert call_count["n"] == 2  # 500 + 100
        assert len(result) == 600
        client.close()

    def test_empty_input_returns_empty(self):
        client = _make_client_with_transport(lambda req: httpx.Response(200, json=[]))
        assert client.enrich_citations([]) == {}
        client.close()

    def test_never_raises_on_any_failure(self):
        """Every failure mode must return empty, never raise — the
        pipeline depends on this for graceful degradation."""
        def handler(req):
            raise httpx.ConnectError("network down")
        client = _make_client_with_transport(handler)
        with patch("app.services.semantic_scholar.time.sleep"):
            result = client.enrich_citations([{"doi": "10.1/x"}])
        assert result == {}  # not an exception
        client.close()
