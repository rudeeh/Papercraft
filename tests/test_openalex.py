"""Tests for app/services/openalex.py."""

import httpx
import pytest

from app.services.openalex import (
    OpenAlexClient,
    TITLE_MATCH_THRESHOLD,
    normalize_arxiv_id,
    normalize_doi,
    normalize_work,
    title_similarity,
)

ATTENTION_WORK = {
    "id": "https://openalex.org/W2963403868",
    "title": "Attention Is All You Need",
    "doi": "https://doi.org/10.5555/3295222.3295349",
    "publication_year": 2017,
    "publication_date": "2017-06-12",
    "type": "article",
    "cited_by_count": 100000,
    "open_access": {"is_oa": True},
    "primary_location": {
        "pdf_url": "https://arxiv.org/pdf/1706.03762",
        "source": {"display_name": "NeurIPS"},
    },
    "abstract_inverted_index": {"The": [0], "dominant": [1], "models": [2]},
    "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    "concepts": [{"display_name": "Machine translation"}, {"display_name": None}],
    "authorships": [
        {
            "author": {"display_name": "Ashish Vaswani", "orcid": None},
            "institutions": [{"display_name": "Google Brain"}],
        },
        {
            "author": {"display_name": "Noam Shazeer", "orcid": None},
            "institutions": [{"display_name": "Google Brain"}],
        },
        {"author": {"display_name": None}, "institutions": []},
    ],
}


def _client(handler) -> OpenAlexClient:
    transport = httpx.MockTransport(handler)
    return OpenAlexClient(
        base_url="https://api.openalex.org",
        mailto="test@example.com",
        client=httpx.Client(transport=transport),
    )


class TestNormalizers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.1234/ABC.def", "10.1234/abc.def"),
            ("https://doi.org/10.1234/abc", "10.1234/abc"),
            ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
            ("not a doi", None),
            ("", None),
            (None, None),
        ],
    )
    def test_normalize_doi(self, raw, expected):
        assert normalize_doi(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2401.12345", "2401.12345"),
            ("arXiv:1706.03762", "1706.03762"),
            ("arxiv:1706.03762v5", "1706.03762v5"),
            ("cs.CV/0701001", "cs.CV/0701001"),
            ("nonsense", None),
            (None, None),
        ],
    )
    def test_normalize_arxiv_id(self, raw, expected):
        assert normalize_arxiv_id(raw) == expected

    def test_title_similarity_ignores_case_and_punctuation(self):
        assert title_similarity("Attention Is All You Need!", "attention is all you need") == 1.0

    def test_negation_is_a_documented_blind_spot(self):
        """
        Character similarity cannot see negation: these two *different*
        papers score above the threshold. Pinned as a test so the
        limitation stays visible -- it is why title hits are labelled
        ``match_method="title"`` rather than trusted like a DOI.
        """
        score = title_similarity("Attention Is All You Need", "Attention Is Not All You Need")
        assert score >= TITLE_MATCH_THRESHOLD

    def test_title_similarity_empty_is_zero(self):
        assert title_similarity("", "anything") == 0.0


class TestNormalizeWork:
    def test_flattens_expected_fields(self):
        out = normalize_work(ATTENTION_WORK)
        assert out["title"] == "Attention Is All You Need"
        assert out["doi"] == "10.5555/3295222.3295349"
        assert out["year"] == 2017
        assert out["venue"] == "NeurIPS"
        assert out["metadata_source"] == "openalex"
        assert out["cited_by_count"] == 100000

    def test_reconstructs_abstract_from_inverted_index(self):
        assert normalize_work(ATTENTION_WORK)["abstract"] == "The dominant models"

    def test_missing_abstract_is_none(self):
        assert normalize_work({"title": "x"})["abstract"] is None

    def test_drops_authors_without_names(self):
        authors = normalize_work(ATTENTION_WORK)["authors"]
        assert [a["name"] for a in authors] == ["Ashish Vaswani", "Noam Shazeer"]

    def test_institutions_deduplicated_preserving_order(self):
        assert normalize_work(ATTENTION_WORK)["institutions"] == ["Google Brain"]

    def test_drops_unnamed_concepts(self):
        assert normalize_work(ATTENTION_WORK)["concepts"] == ["Machine translation"]


class TestLookups:
    def test_by_doi_hits_the_works_path(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json=ATTENTION_WORK)

        work = _client(handler).by_doi("10.5555/3295222.3295349")
        assert work["title"] == "Attention Is All You Need"
        assert "doi.org/10.5555/3295222.3295349" in seen["url"]
        assert "mailto=test%40example.com" in seen["url"]

    def test_by_doi_returns_none_for_a_non_doi(self):
        def handler(request):  # pragma: no cover - must not be called
            raise AssertionError("should not have made a request")

        assert _client(handler).by_doi("not-a-doi") is None

    def test_404_returns_none(self):
        assert _client(lambda r: httpx.Response(404, json={})).by_doi("10.1/x") is None

    def test_rate_limit_returns_none(self):
        assert _client(lambda r: httpx.Response(429, text="slow down")).by_doi("10.1/x") is None

    def test_network_error_returns_none(self):
        def handler(request):
            raise httpx.ConnectError("no route to host")

        assert _client(handler).by_doi("10.1/x") is None

    def test_unparseable_body_returns_none(self):
        assert _client(lambda r: httpx.Response(200, text="<html>")).by_doi("10.1/x") is None

    def test_by_arxiv_id_takes_first_search_result(self):
        payload = {"results": [ATTENTION_WORK]}
        work = _client(lambda r: httpx.Response(200, json=payload)).by_arxiv_id("1706.03762")
        assert work["title"] == "Attention Is All You Need"

    def test_by_arxiv_id_with_no_results(self):
        assert _client(lambda r: httpx.Response(200, json={"results": []})).by_arxiv_id("1706.03762") is None

    def test_by_title_accepts_a_close_match(self):
        payload = {"results": [ATTENTION_WORK]}
        client = _client(lambda r: httpx.Response(200, json=payload))
        assert client.by_title("Attention is all you need") is not None

    def test_by_title_rejects_a_different_paper(self):
        payload = {"results": [{"title": "A Completely Unrelated Survey of Robotics"}]}
        client = _client(lambda r: httpx.Response(200, json=payload))
        assert client.by_title("Attention Is All You Need") is None

    def test_by_title_refuses_titles_too_short_to_identify(self):
        def handler(request):  # pragma: no cover - must not be called
            raise AssertionError("should not have made a request")

        assert _client(handler).by_title("Intro") is None


class TestResolveAndEnrich:
    def test_resolve_prefers_doi_over_everything_else(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json=ATTENTION_WORK)

        client = _client(handler)
        work, method = client.resolve(
            doi="10.5555/3295222.3295349",
            arxiv_id="1706.03762",
            title="Attention Is All You Need",
        )
        assert method == "doi"
        assert len(calls) == 1 and "doi.org" in calls[0]

    def test_resolve_falls_through_to_arxiv_when_doi_missing(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={"results": [ATTENTION_WORK]})

        client = _client(handler)
        resolved = client.resolve(arxiv_id="1706.03762")
        assert resolved is not None and resolved[1] == "arxiv"
        # The locator is percent-encoded into the filter query parameter.
        assert "arxiv.org%2Fabs%2F1706.03762" in calls[0]

    def test_resolve_falls_through_to_title_when_identifiers_are_junk(self):
        client = _client(lambda r: httpx.Response(200, json={"results": [ATTENTION_WORK]}))
        resolved = client.resolve(doi="not-a-doi", arxiv_id="junk", title="Attention Is All You Need")
        assert resolved is not None and resolved[1] == "title"

    def test_enrich_returns_normalized_shape(self):
        client = _client(lambda r: httpx.Response(200, json=ATTENTION_WORK))
        enriched = client.enrich(doi="10.5555/3295222.3295349")
        assert enriched["year"] == 2017
        assert enriched["authors"][0]["name"] == "Ashish Vaswani"

    def test_doi_match_is_reported_as_certain(self):
        client = _client(lambda r: httpx.Response(200, json=ATTENTION_WORK))
        enriched = client.enrich(doi="10.5555/3295222.3295349")
        assert enriched["match_method"] == "doi"
        assert enriched["match_confidence"] == 1.0

    def test_title_match_carries_its_similarity_not_certainty(self):
        payload = {"results": [ATTENTION_WORK]}
        client = _client(lambda r: httpx.Response(200, json=payload))
        enriched = client.enrich(title="Attention is all you need")
        assert enriched["match_method"] == "title"
        assert TITLE_MATCH_THRESHOLD <= enriched["match_confidence"] <= 1.0

    def test_enrich_returns_none_when_nothing_resolves(self):
        client = _client(lambda r: httpx.Response(404, json={}))
        assert client.enrich(doi="10.1234/x", title="Some Paper Title Here") is None

    def test_no_identifiers_makes_no_requests(self):
        def handler(request):  # pragma: no cover - must not be called
            raise AssertionError("should not have made a request")

        assert _client(handler).enrich() is None
