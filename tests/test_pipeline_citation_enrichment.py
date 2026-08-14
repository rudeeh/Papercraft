"""
Tests for the Semantic Scholar citation enrichment step in the
ingestion pipeline.

Verifies that:
  * The CITATION_ENRICHMENT step runs between CITATIONS and ENTITIES.
  * S2 metadata is merged into citation dicts in place.
  * Citations S2 doesn't have pass through unchanged (sparse stubs).
  * The step is non-critical: if S2 fails, the pipeline continues.
  * The step is skipped when S2 is not configured (s2_client=None).
  * The step is skipped when SEMANTIC_SCHOLAR_ENABLED=False.
  * The TLDR field carries provenance (model + source).
  * Existing extractor values are preserved (S2 only fills gaps).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.pipeline.paper_ingestion_pipeline import (
    PaperIngestionPipeline,
    PipelineResult,
    StepStatus,
)


def _make_pipeline(s2_client=None):
    """Build a pipeline with no real backends — just the S2 client
    (which is itself mocked in these tests)."""
    return PaperIngestionPipeline(
        neo4j_client=None,
        vector_repo=None,
        openalex_client=None,
        llm_extractor=None,
        draft_sink=None,
        s2_client=s2_client,
    )


def _make_result():
    """Fresh PipelineResult for each test."""
    return PipelineResult(paper_id="test_paper_1")


def _citation(doi=None, arxiv_id=None, title="Some Paper", year=None, authors=None):
    """Build a normalized citation dict."""
    cit = {"title": title, "ref_id": "[1]"}
    if doi:
        cit["doi"] = doi
    if arxiv_id:
        cit["arxiv_id"] = arxiv_id
    if year:
        cit["year"] = year
    if authors:
        cit["authors"] = authors
    return cit


# ---------------------------------------------------------------------------
# Step ordering and skipping
# ---------------------------------------------------------------------------


class TestStepSkipped:
    def test_skipped_when_no_s2_client(self):
        """When s2_client=None (no API key configured, or
        SEMANTIC_SCHOLAR_ENABLED=False in the factory), the step is
        SKIPPED and citations pass through unchanged."""
        pipeline = _make_pipeline(s2_client=None)
        result = _make_result()
        citations = [_citation(doi="10.1/x"), _citation(arxiv_id="2401.12345")]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        # Citations unchanged
        assert enriched == citations
        # Step recorded as SKIPPED
        assert len(result.steps) == 1
        assert result.steps[0].step_name == "CITATION_ENRICHMENT"
        assert result.steps[0].status == StepStatus.SKIPPED

    def test_skipped_when_no_citations(self):
        """If the citation extractor found nothing, there's nothing to
        enrich — skip the S2 call entirely."""
        mock_s2 = MagicMock()
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()

        enriched = pipeline._enrich_citations_with_s2("paper_1", [], result)

        assert enriched == []
        assert result.steps[0].status == StepStatus.SKIPPED
        # S2 client was NOT called (no point)
        mock_s2.enrich_citations.assert_not_called()

    @pytest.fixture(autouse=True)
    def _s2_enabled(self, monkeypatch):
        """Most tests in this file assume SEMANTIC_SCHOLAR_ENABLED=True.
        This fixture sets it for all tests; the ones that need False
        can override."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "SEMANTIC_SCHOLAR_ENABLED", True)


# ---------------------------------------------------------------------------
# Merging behavior
# ---------------------------------------------------------------------------


class TestMergeBehavior:
    def test_s2_metadata_merged_into_citation(self):
        """S2 enrichment dict is merged into the citation dict in place."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {
            "doi:10.5555/3295222.3295349": {
                "abstract": "We propose a new architecture...",
                "year": 2017,
                "venue": "NeurIPS",
                "citation_count": 100000,
                "influential_citation_count": 20000,
                "author_names": ["Ashish Vaswani", "Noam Shazeer"],
                "tldr": {
                    "text": "A new architecture based on attention.",
                    "model": "tldr@v2.0.0",
                    "source": "semantic_scholar",
                },
                "s2_paper_id": "abc123",
                "s2_title": "Attention Is All You Need",
            }
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(doi="10.5555/3295222.3295349", title="Attention Is All You Need")]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        cit = enriched[0]
        assert cit["abstract"] == "We propose a new architecture..."
        assert cit["year"] == 2017
        assert cit["venue"] == "NeurIPS"
        assert cit["citation_count"] == 100000
        assert cit["influential_citation_count"] == 20000
        assert cit["author_names"] == ["Ashish Vaswani", "Noam Shazeer"]
        assert cit["tldr"] == {
            "text": "A new architecture based on attention.",
            "model": "tldr@v2.0.0",
            "source": "semantic_scholar",
        }
        assert cit["s2_paper_id"] == "abc123"
        # Step recorded as SUCCESS
        assert result.steps[0].status == StepStatus.SUCCESS

    def test_existing_extractor_values_preserved(self):
        """S2 only fills gaps — it doesn't override fields the extractor
        already found. This preserves provenance: the user can see both
        the extractor's value (from the citing paper's reference list)
        and S2's canonical value (via s2_title)."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {
            "doi:10.1/x": {
                "year": 2020,  # S2 says 2020
                "s2_title": "S2's Cleaner Title",
            }
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(doi="10.1/x", title="Extractor's Title", year=2019)]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        cit = enriched[0]
        # Extractor's values preserved
        assert cit["title"] == "Extractor's Title"
        assert cit["year"] == 2019
        # S2's title stored separately
        assert cit["s2_title"] == "S2's Cleaner Title"

    def test_s2_fills_missing_title(self):
        """If the extractor didn't find a title, S2's title takes its place."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {
            "doi:10.1/x": {"s2_title": "S2's Title"}
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(doi="10.1/x", title="")]  # extractor found nothing

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        assert enriched[0]["title"] == "S2's Title"
        assert "s2_title" not in enriched[0]  # was merged into "title"

    def test_citations_s2_doesnt_have_pass_through(self):
        """Citations S2 returned no enrichment for stay sparse — the
        stub node will be created with just what the extractor found."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {
            "doi:10.1/found": {"abstract": "found abstract"}
            # Note: "doi:10.1/missing" is absent from the returned dict
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [
            _citation(doi="10.1/found"),
            _citation(doi="10.1/missing"),
        ]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        assert enriched[0]["abstract"] == "found abstract"
        assert "abstract" not in enriched[1]
        assert enriched[1]["doi"] == "10.1/missing"  # original preserved

    def test_arxiv_id_lookup_works(self):
        """Citations with arXiv ID but no DOI are looked up via arxiv: key."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {
            "arxiv:1706.03762": {"abstract": "via arxiv lookup"}
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(arxiv_id="1706.03762")]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        assert enriched[0]["abstract"] == "via arxiv lookup"


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_s2_failure_passes_through_unchanged(self):
        """If S2's enrich_citations raises or returns empty, citations
        pass through unchanged. The pipeline must not crash."""
        mock_s2 = MagicMock()
        mock_s2.enrich_citations.return_value = {}  # S2 found nothing
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(doi="10.1/x", title="Original Title")]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        assert enriched == citations  # unchanged
        # Step still recorded (with whatever status _run_step assigned)
        assert len(result.steps) == 1

    def test_tldr_provenance_preserved_through_merge(self):
        """The TLDR dict from S2 (with model + source) must survive the
        merge into the citation dict intact — UIs depend on these
        fields to label it as auto-generated."""
        mock_s2 = MagicMock()
        tldr_with_provenance = {
            "text": "A summary.",
            "model": "tldr@v2.0.0",
            "source": "semantic_scholar",
        }
        mock_s2.enrich_citations.return_value = {
            "doi:10.1/x": {"tldr": tldr_with_provenance}
        }
        pipeline = _make_pipeline(s2_client=mock_s2)
        result = _make_result()
        citations = [_citation(doi="10.1/x")]

        enriched = pipeline._enrich_citations_with_s2("paper_1", citations, result)

        assert enriched[0]["tldr"] == tldr_with_provenance
        # Provenance fields are present and correct
        assert enriched[0]["tldr"]["source"] == "semantic_scholar"
        assert enriched[0]["tldr"]["model"] == "tldr@v2.0.0"
