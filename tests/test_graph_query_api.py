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


async def _get_citation_graph():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.get("/api/v1/citation-graph")


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
    async def test_passes_request_api_key_to_answer_generator(self):
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = RETRIEVAL_RESULT
        mock_generator = MagicMock()
        mock_generator.generate.return_value = GENERATED_ANSWER
        app.dependency_overrides[get_hybrid_retriever] = lambda: mock_hybrid
        app.dependency_overrides[get_answer_generator] = lambda: mock_generator

        await _post_graph_query({"query": "What is BERT?", "api_key": "sk-or-user-supplied"})

        mock_generator.generate.assert_called_once_with(
            "What is BERT?", RETRIEVAL_RESULT, api_key="sk-or-user-supplied"
        )

    @pytest.mark.asyncio
    async def test_no_api_key_available_returns_401(self):
        from app.services.llm import LLMNotConfiguredError

        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = RETRIEVAL_RESULT
        mock_generator = MagicMock()
        mock_generator.generate.side_effect = LLMNotConfiguredError("no key")
        app.dependency_overrides[get_hybrid_retriever] = lambda: mock_hybrid
        app.dependency_overrides[get_answer_generator] = lambda: mock_generator

        response = await _post_graph_query({"query": "What is BERT?"})

        assert response.status_code == 401

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


class TestCitationGraphEndpoint:
    @pytest.mark.asyncio
    async def test_returns_papers_and_edges(self):
        mock_repo = MagicMock()
        mock_repo.get_citation_graph.return_value = {
            "papers": [
                {"paper_id": "p1", "title": "Paper One", "name": "Paper One", "year": 2021, "is_stub": False},
                {"paper_id": "p2", "title": "Paper Two", "name": "Paper Two", "year": 2022, "is_stub": False},
            ],
            "edges": [{"source": "p2", "target": "p1"}],
        }
        app.dependency_overrides[get_graph_repository] = lambda: mock_repo

        response = await _get_citation_graph()

        assert response.status_code == 200
        body = response.json()
        assert len(body["papers"]) == 2
        assert body["edges"] == [{"source": "p2", "target": "p1"}]

    @pytest.mark.asyncio
    async def test_graph_store_failure_returns_503(self):
        mock_repo = MagicMock()
        mock_repo.get_citation_graph.side_effect = ConnectionError("neo4j down")
        app.dependency_overrides[get_graph_repository] = lambda: mock_repo

        response = await _get_citation_graph()

        assert response.status_code == 503
