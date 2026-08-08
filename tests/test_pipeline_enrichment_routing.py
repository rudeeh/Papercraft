"""
Tests for the enrichment and confidence-routing stages added to
PaperIngestionPipeline.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.pipeline.paper_ingestion_pipeline import PaperIngestionPipeline, StepStatus

# Structured enough for the parser to find an Abstract, which is what the
# heuristic EntityExtractor scans -- unstructured text yields no entities
# and the routing assertions below would be vacuous.
PAGES = [
    (
        1,
        "Attention Is All You Need\n\n"
        "Abstract\n\n"
        "We propose the Transformer, a model architecture eschewing recurrence "
        "and based entirely on attention mechanisms.\n\n"
        "1 Introduction\n\n"
        "The Transformer is evaluated on the WMT14 English-to-German translation "
        "task and reaches 28.4 BLEU, improving on previous best results.\n",
    ),
]

LLM_EVIDENCE = (
    "We propose the Transformer, a model architecture eschewing recurrence "
    "and based entirely on attention mechanisms."
)

OPENALEX_RECORD = {
    "openalex_id": "https://openalex.org/W1",
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "doi": "10.5555/3295222.3295349",
    "year": 2017,
    "venue": "NeurIPS",
    "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
    "match_method": "doi",
    "match_confidence": 1.0,
}


@pytest.fixture
def mock_ocr():
    return lambda path: PAGES


@pytest.fixture
def restore_settings():
    original = settings.EXTRACTION_PROVIDER
    yield
    settings.EXTRACTION_PROVIDER = original


def run(pipeline, mock_ocr, paper_id="p1"):
    with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
        return pipeline.process(paper_id=paper_id, file_path="/fake.pdf")


class TestEnrichment:
    def test_skipped_when_no_client_is_supplied(self, mock_ocr):
        result = run(PaperIngestionPipeline(), mock_ocr)
        step = result.get_step("ENRICHMENT")
        assert step.status == StepStatus.SKIPPED
        assert result.enriched is False

    def test_successful_enrichment_is_recorded(self, mock_ocr):
        client = MagicMock()
        client.enrich.return_value = OPENALEX_RECORD
        result = run(PaperIngestionPipeline(openalex_client=client), mock_ocr)
        assert result.get_step("ENRICHMENT").status == StepStatus.SUCCESS
        assert result.enriched is True

    def test_enriched_metadata_reaches_the_paper_node(self, mock_ocr):
        client = MagicMock()
        client.enrich.return_value = OPENALEX_RECORD
        pipeline = PaperIngestionPipeline(openalex_client=client)
        result = run(pipeline, mock_ocr)

        nodes = result.get_step("GRAPH_BUILD").data["nodes"]
        paper = next(n for n in nodes if n.node_type == "Paper" and not n.properties.get("is_stub"))
        assert paper.properties["title"] == "Attention Is All You Need"
        assert paper.properties["year"] == 2017
        assert paper.properties["author_names"] == ["Ashish Vaswani", "Noam Shazeer"]

    def test_a_miss_leaves_the_parsers_values_alone(self, mock_ocr):
        client = MagicMock()
        client.enrich.return_value = None
        result = run(PaperIngestionPipeline(openalex_client=client), mock_ocr)
        assert result.enriched is False
        assert result.status != "FAILED"

    def test_a_network_failure_does_not_fail_ingestion(self, mock_ocr):
        client = MagicMock()
        client.enrich.side_effect = RuntimeError("connection refused")
        result = run(PaperIngestionPipeline(openalex_client=client), mock_ocr)
        assert result.get_step("ENRICHMENT").status == StepStatus.ERROR
        assert result.status == "PARTIAL"  # not FAILED

    def test_author_nodes_come_from_enrichment(self, mock_ocr):
        client = MagicMock()
        client.enrich.return_value = OPENALEX_RECORD
        result = run(PaperIngestionPipeline(openalex_client=client), mock_ocr)
        authors = {
            n.name for n in result.get_step("GRAPH_BUILD").data["nodes"] if n.node_type == "Author"
        }
        assert authors == {"Ashish Vaswani", "Noam Shazeer"}


class TestConfidenceRouting:
    def test_routing_always_runs(self, mock_ocr):
        result = run(PaperIngestionPipeline(), mock_ocr)
        assert result.get_step("CONFIDENCE_ROUTING").status == StepStatus.SUCCESS

    def test_without_a_sink_nothing_is_withheld_from_the_graph(self, mock_ocr):
        """
        Withholding review-needed items with nowhere to queue them would
        silently delete them -- strictly worse than the pre-routing
        behaviour of inserting everything.
        """
        result = run(PaperIngestionPipeline(), mock_ocr)
        queue_step = result.get_step("DRAFT_QUEUE")
        assert queue_step.status == StepStatus.SKIPPED
        assert "inserted directly" in queue_step.error

        entity_names = {
            n.name
            for n in result.get_step("GRAPH_BUILD").data["nodes"]
            if n.node_type in {"Method", "Dataset", "Task", "Metric"}
        }
        assert entity_names  # the graph is not empty

    def test_with_a_sink_low_confidence_items_are_queued(self, mock_ocr):
        queued = []

        def sink(paper_id, routed):
            queued.extend(routed)
            return routed

        llm = MagicMock()
        llm.extract.return_value = {
            "entities": [{
                "name": "SpeculativeMethod",
                "type": "Method",
                "evidence": LLM_EVIDENCE,
                "confidence": 0.6,
            }],
            "relations": [],
        }
        settings.EXTRACTION_PROVIDER = "hybrid"
        try:
            pipeline = PaperIngestionPipeline(llm_extractor=llm, draft_sink=sink)
            result = run(pipeline, mock_ocr)
        finally:
            settings.EXTRACTION_PROVIDER = "heuristic"

        assert result.queued_for_review_count == len(queued) >= 1
        assert all(item.needs_review for item in queued)

    def test_with_a_sink_only_auto_insert_items_reach_the_graph(self, mock_ocr):
        llm = MagicMock()
        llm.extract.return_value = {
            "entities": [{
                "name": "SpeculativeMethod",
                "type": "Method",
                "evidence": LLM_EVIDENCE,
                "confidence": 0.6,
            }],
            "relations": [],
        }
        settings.EXTRACTION_PROVIDER = "hybrid"
        try:
            pipeline = PaperIngestionPipeline(llm_extractor=llm, draft_sink=lambda p, r: r)
            result = run(pipeline, mock_ocr)
        finally:
            settings.EXTRACTION_PROVIDER = "heuristic"

        names = {n.name for n in result.get_step("GRAPH_BUILD").data["nodes"]}
        assert "SpeculativeMethod" not in names

    def test_a_failing_sink_inserts_everything_rather_than_losing_it(self, mock_ocr):
        def broken_sink(paper_id, routed):
            raise RuntimeError("postgres is down")

        llm = MagicMock()
        llm.extract.return_value = {
            "entities": [{
                "name": "SpeculativeMethod",
                "type": "Method",
                "evidence": LLM_EVIDENCE,
                "confidence": 0.6,
            }],
            "relations": [],
        }
        settings.EXTRACTION_PROVIDER = "hybrid"
        try:
            pipeline = PaperIngestionPipeline(llm_extractor=llm, draft_sink=broken_sink)
            result = run(pipeline, mock_ocr)
        finally:
            settings.EXTRACTION_PROVIDER = "heuristic"

        assert result.get_step("DRAFT_QUEUE").status == StepStatus.ERROR
        assert result.queued_for_review_count == 0
        names = {n.name for n in result.get_step("GRAPH_BUILD").data["nodes"]}
        assert "SpeculativeMethod" in names


class TestLLMExtractionStage:
    def test_skipped_by_default(self, mock_ocr):
        result = run(PaperIngestionPipeline(), mock_ocr)
        step = result.get_step("LLM_EXTRACTION")
        assert step.status == StepStatus.SKIPPED
        assert "heuristic" in step.error

    def test_skipped_when_no_extractor_supplied_even_in_llm_mode(
        self, mock_ocr, restore_settings
    ):
        settings.EXTRACTION_PROVIDER = "llm"
        result = run(PaperIngestionPipeline(), mock_ocr)
        assert result.get_step("LLM_EXTRACTION").status == StepStatus.SKIPPED

    def test_llm_mode_replaces_the_heuristic_pass(self, mock_ocr, restore_settings):
        llm = MagicMock()
        llm.extract.return_value = {
            "entities": [{
                "name": "OnlyFromTheModel",
                "type": "Method",
                "evidence": LLM_EVIDENCE,
                "confidence": 0.95,
            }],
            "relations": [],
        }
        settings.EXTRACTION_PROVIDER = "llm"
        result = run(PaperIngestionPipeline(llm_extractor=llm), mock_ocr)

        names = {
            n.name
            for n in result.get_step("GRAPH_BUILD").data["nodes"]
            if n.node_type == "Method"
        }
        assert names == {"OnlyFromTheModel"}

    def test_hybrid_mode_keeps_both_passes(self, mock_ocr, restore_settings):
        llm = MagicMock()
        llm.extract.return_value = {
            "entities": [{
                "name": "OnlyFromTheModel",
                "type": "Method",
                "evidence": LLM_EVIDENCE,
                "confidence": 0.95,
            }],
            "relations": [],
        }
        settings.EXTRACTION_PROVIDER = "hybrid"
        result = run(PaperIngestionPipeline(llm_extractor=llm), mock_ocr)

        names = {
            n.name
            for n in result.get_step("GRAPH_BUILD").data["nodes"]
            if n.node_type == "Method"
        }
        assert "OnlyFromTheModel" in names
        assert len(names) > 1  # the heuristic pass survived too

    def test_an_llm_failure_falls_back_to_the_heuristic_pass(
        self, mock_ocr, restore_settings
    ):
        llm = MagicMock()
        llm.extract.side_effect = RuntimeError("model timeout")
        settings.EXTRACTION_PROVIDER = "hybrid"
        result = run(PaperIngestionPipeline(llm_extractor=llm), mock_ocr)

        assert result.get_step("LLM_EXTRACTION").status == StepStatus.ERROR
        assert result.status != "FAILED"
        assert result.get_step("GRAPH_BUILD").status == StepStatus.SUCCESS
