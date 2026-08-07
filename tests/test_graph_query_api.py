"""Tests for the Phase 16 graph query API routes."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from app.api.main import app
from app.api.graph_routes import (
    get_hybrid_retriever,
    get_answer_generator,
    get_graph_repository,
)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


RETRIEVAL_RESULT = {
    "query": "What is BERT?",
    "query_type": "ENTITY_LOOKUP",
    "graph_facts": [{
        "subject": {"name": "BERT", "type": "Method", "paper_id": "p1"},
        "relation": "IMPROVES_UPON",
        "object": {"name": "ELMo", "type": "Method", "paper_id": None},
        "evidence": "BERT improves upon ELMo.",
        "source_paper_ids": ["p1"],
    }],
    "vector_results": [],
    "citation_paths": [],
    "source_paper_ids": ["p1"],
}

GENERATED_ANSWER = {
    "answer": "BERT is a bidirectional transformer model.",
    "sources": [{"paper_id": "p1", "title": "BERT paper"}],
    "graph_facts_used": RETRIEVAL_RESULT["graph_facts"],
    "confidence_notes": [],
}


async def _post_graph_query(payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post("/api/v1/graph-query", json=payload)


async def _get_paper_graph(paper_id):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get(f"/api/v1/papers/{paper_id}/graph")


class TestGraphQueryEndpoint:
    @pytest.mark.asyncio
    async def test_returns_answer_sources_and_trace(self):
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = RETRIEVAL_RESULT
        mock_generator = MagicMock()
        mock_generator.generate.return_value = GENERATED_ANSWER

        app.dependency_overrides[get_hybrid_retriever] = lambda: mock_hybrid
        app.dependency_overrides[get_answer_generator] = lambda: mock_generator

        response = await _post_graph_query({"query": "What is BERT?", "project_id": "proj1", "top_k": 5})

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == GENERATED_ANSWER["answer"]
        assert body["sources"] == [{"paper_id": "p1", "title": "BERT paper"}]
        assert body["retrieval_trace"]["query_type"] == "ENTITY_LOOKUP"
        assert body["retrieval_trace"]["graph_facts"] == RETRIEVAL_RESULT["graph_facts"]
        assert body["retrieval_trace"]["source_paper_ids"] == ["p1"]

        mock_hybrid.retrieve.assert_called_once_with("What is BERT?", top_k=5)

    @pytest.mark.asyncio
    async def test_blank_query_returns_422(self):
        app.dependency_overrides[get_hybrid_retriever] = lambda: MagicMock()
        app.dependency_overrides[get_answer_generator] = lambda: MagicMock()

        response = await _post_graph_query({"query": "   "})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_query_field_returns_422(self):
        app.dependency_overrides[get_hybrid_retriever] = lambda: MagicMock()
        app.dependency_overrides[get_answer_generator] = lambda: MagicMock()

        response = await _post_graph_query({"project_id": "proj1"})

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_retrieval_failure_returns_503(self):
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.side_effect = ConnectionError("neo4j down")
        app.dependency_overrides[get_hybrid_retriever] = lambda: mock_hybrid
        app.dependency_overrides[get_answer_generator] = lambda: MagicMock()

        response = await _post_graph_query({"query": "What is BERT?"})

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_default_top_k_used_when_omitted(self):
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = RETRIEVAL_RESULT
        mock_generator = MagicMock()
        mock_generator.generate.return_value = GENERATED_ANSWER
        app.dependency_overrides[get_hybrid_retriever] = lambda: mock_hybrid
        app.dependency_overrides[get_answer_generator] = lambda: mock_generator

        await _post_graph_query({"query": "What is BERT?"})

        mock_hybrid.retrieve.assert_called_once_with("What is BERT?", top_k=10)


class TestPaperGraphEndpoint:
    @pytest.mark.asyncio
    async def test_returns_nodes_and_edges_for_existing_paper(self):
        mock_repo = MagicMock()
        mock_repo.get_paper_graph.return_value = {
            "nodes": [{"id": "p1", "type": "Paper", "title": "My Paper"}],
            "edges": [],
        }
        app.dependency_overrides[get_graph_repository] = lambda: mock_repo

        response = await _get_paper_graph("p1")

        assert response.status_code == 200
        body = response.json()
        assert body["paper_id"] == "p1"
        assert body["nodes"][0]["id"] == "p1"
        assert body["edges"] == []

    @pytest.mark.asyncio
    async def test_missing_paper_returns_404(self):
        mock_repo = MagicMock()
        mock_repo.get_paper_graph.return_value = {}
        app.dependency_overrides[get_graph_repository] = lambda: mock_repo

        response = await _get_paper_graph("does-not-exist")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_graph_store_failure_returns_503(self):
        mock_repo = MagicMock()
        mock_repo.get_paper_graph.side_effect = ConnectionError("neo4j down")
        app.dependency_overrides[get_graph_repository] = lambda: mock_repo

        response = await _get_paper_graph("p1")

        assert response.status_code == 503
