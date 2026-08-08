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
            "GRAPH_BUILD", "NEO4J_STORE", "CHUNKING", "EMBEDDING", "VECTOR_STORE",
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

    def test_resolve_citation_stub_called_with_papers_own_arxiv_id(self):
        """
        _try_resolve_stubs must use the ingested paper's OWN arxiv_id/doi
        (from the parser's first-page self-identity extraction), not the
        arxiv_id/doi of papers it cites -- using the latter would resolve
        the wrong stub (the one for a paper THIS one cites) and rewire it
        into a self-loop instead of leaving it for whichever paper it
        actually represents.
        """
        mock_ocr = lambda path: [
            (1, "arXiv:2101.00001\n\nSelf-Identified Paper\n\nAbstract\nSome text."),
            (2, 'References\n[1] Other Author. "Some Other Paper." arXiv:1706.03762'),
        ]
        mock_client = MagicMock()
        pipeline = PaperIngestionPipeline(neo4j_client=mock_client)
        pipeline._graph_repo.resolve_citation_stub = MagicMock()
        # No pre-existing real paper for the cited work -- isolates this
        # test to the "own identity" resolution direction only.
        pipeline._graph_repo.find_real_paper_id = MagicMock(return_value=None)

        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            pipeline.process(paper_id="p1", file_path="/fake.pdf")

        called_ids = [c.args[0] for c in pipeline._graph_repo.resolve_citation_stub.call_args_list]
        # Resolved using THIS paper's own arXiv ID (2101.00001) ...
        assert "arxiv_2101.00001" in called_ids
        # ... never using the arXiv ID of the paper it cites (1706.03762).
        assert "arxiv_1706.03762" not in called_ids
        for c in pipeline._graph_repo.resolve_citation_stub.call_args_list:
            assert c.args[1] == "p1"

    def test_resolves_citation_to_an_already_real_paper(self):
        """
        When this paper cites a paper that already exists as a real
        (non-stub) node -- the reverse ingestion order from the test above
        -- the stub PaperGraphBuilder just created for that citation should
        be resolved to the existing real node right away.
        """
        mock_ocr = lambda path: [
            (1, "A Later Paper\n\nAbstract\nSome text."),
            (2, 'References\n[1] Other Author. "Earlier Paper." arXiv:1706.03762'),
        ]
        mock_client = MagicMock()
        pipeline = PaperIngestionPipeline(neo4j_client=mock_client)
        pipeline._graph_repo.resolve_citation_stub = MagicMock()
        pipeline._graph_repo.find_real_paper_id = MagicMock(return_value="earlier_paper_real_id")

        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            pipeline.process(paper_id="p1", file_path="/fake.pdf")

        pipeline._graph_repo.find_real_paper_id.assert_any_call(arxiv_id="1706.03762")
        pipeline._graph_repo.resolve_citation_stub.assert_any_call(
            "arxiv_1706.03762", "earlier_paper_real_id"
        )

    def test_does_not_resolve_citation_to_itself(self):
        """If find_real_paper_id somehow returns this paper's own id (e.g. a
        paper citing a preprint of itself), resolution must be skipped --
        never rewire a stub to the citing paper itself."""
        mock_ocr = lambda path: [
            (1, "A Paper\n\nAbstract\nSome text."),
            (2, 'References\n[1] Other Author. "Something." arXiv:1706.03762'),
        ]
        mock_client = MagicMock()
        pipeline = PaperIngestionPipeline(neo4j_client=mock_client)
        pipeline._graph_repo.resolve_citation_stub = MagicMock()
        pipeline._graph_repo.find_real_paper_id = MagicMock(return_value="p1")

        with patch("app.services.ocr.extract_text_from_pdf", mock_ocr):
            pipeline.process(paper_id="p1", file_path="/fake.pdf")

        called_ids = [c.args[0] for c in pipeline._graph_repo.resolve_citation_stub.call_args_list]
        assert "arxiv_1706.03762" not in called_ids