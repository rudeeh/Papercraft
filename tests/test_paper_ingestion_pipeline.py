"""Tests for PaperIngestionPipeline (Phase 10)."""

import pytest
from unittest.mock import MagicMock, patch

from app.pipeline.paper_ingestion_pipeline import (
    PaperIngestionPipeline,
    PipelineResult,
    StepResult,
    StepStatus,
)


class TestPipelineResult:
    def test_all_success(self):
        result = PipelineResult(paper_id="p1")
        result.steps = [
            StepResult(step_name="OCR", status=StepStatus.SUCCESS),
            StepResult(step_name="PARSING", status=StepStatus.SUCCESS),
            StepResult(step_name="ENTITIES", status=StepStatus.SUCCESS),
        ]
        assert result.status == "COMPLETED"

    def test_critical_failure(self):
        result = PipelineResult(paper_id="p1")
        result.steps = [
            StepResult(step_name="OCR", status=StepStatus.ERROR, error="boom"),
        ]
        assert result.status == "FAILED"

    def test_non_critical_failure(self):
        result = PipelineResult(paper_id="p1")
        result.steps = [
            StepResult(step_name="OCR", status=StepStatus.SUCCESS),
            StepResult(step_name="PARSING", status=StepStatus.SUCCESS),
            StepResult(step_name="ENTITIES", status=StepStatus.ERROR, error="skip"),
        ]
        assert result.status == "PARTIAL"

    def test_get_step(self):
        result = PipelineResult(paper_id="p1")
        result.steps = [
            StepResult(step_name="OCR", status=StepStatus.SUCCESS),
            StepResult(step_name="PARSING", status=StepStatus.ERROR, error="fail"),
        ]
        step = result.get_step("PARSING")
        assert step is not None
        assert step.error == "fail"
        assert result.get_step("NONEXISTENT") is None


class TestPaperIngestionPipeline:
    @pytest.fixture
    def mock_ocr(self):
        """Mock OCR to return test pages."""
        return lambda path: [(1, "This is a test paper about BERT."), (2, "We evaluated on SQuAD.")]

    @pytest.fixture
    def pipeline_no_neo4j(self):
        return PaperIngestionPipeline(neo4j_client=None)

    def test_pipeline_runs_without_neo4j(self, pipeline_no_neo4j, mock_ocr):
        """Pipeline should complete (PARTIAL) when Neo4j is not available."""
        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            result = pipeline_no_neo4j.process(
                paper_id="test_pdf", file_path="/fake/test.pdf"
            )

        # OCR and PARSING should succeed (critical)
        ocr_step = result.get_step("OCR")
        parse_step = result.get_step("PARSING")
        assert ocr_step.status == StepStatus.SUCCESS
        assert parse_step.status == StepStatus.SUCCESS

        # Neo4j store should be SKIPPED
        neo4j_step = result.get_step("NEO4J_STORE")
        assert neo4j_step.status == StepStatus.SKIPPED

        # Overall status should not be FAILED
        assert result.status in ("COMPLETED", "PARTIAL")

    def test_pipeline_neo4j_store_skipped_when_no_client(self, pipeline_no_neo4j, mock_ocr):
        """Without Neo4j client, the NEO4J_STORE step is marked SKIPPED."""
        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            result = pipeline_no_neo4j.process(paper_id="p1", file_path="/fake.pdf")

        neo4j_step = result.get_step("NEO4J_STORE")
        assert neo4j_step is not None
        assert neo4j_step.status == StepStatus.SKIPPED
        assert "Neo4j client not configured" in neo4j_step.error

    def test_pipeline_step_tracking(self, pipeline_no_neo4j, mock_ocr):
        """All 10 pipeline steps should appear in the result."""
        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            result = pipeline_no_neo4j.process(paper_id="p1", file_path="/fake.pdf")

        step_names = {s.step_name for s in result.steps}
        expected_steps = {
            "OCR", "PARSING", "CITATIONS", "ENTITIES", "RELATIONS",
            "GRAPH_BUILD", "NEO4J_STORE", "CHUNKING", "EMBEDDING", "QDRANT_STORE",
        }
        assert expected_steps.issubset(step_names)

    def test_pipeline_with_mock_neo4j(self, mock_ocr):
        """Pipeline should store graph when Neo4j is available."""
        mock_client = MagicMock()
        mock_client.connect = MagicMock()
        mock_client.init_schema = MagicMock()
        mock_client.close = MagicMock()

        pipeline = PaperIngestionPipeline(neo4j_client=mock_client)

        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            result = pipeline.process(paper_id="p1", file_path="/fake.pdf")

        neo4j_step = result.get_step("NEO4J_STORE")
        assert neo4j_step is not None
        # Should not be skipped since we provided a Neo4j client
        assert neo4j_step.status != StepStatus.SKIPPED