"""Tests for GraphRepository (Phase 8.2)."""

from unittest.mock import MagicMock

import pytest

from app.storage.graph_repository import GraphRepository


def _make_repo(rows):
    client = MagicMock()
    client.query.return_value = rows
    return GraphRepository(client), client


class TestFindRealPaperId:
    def test_finds_by_doi(self):
        repo, client = _make_repo([{"pid": "real123"}])
        assert repo.find_real_paper_id(doi="10.1234/x") == "real123"
        cypher = client.query.call_args[0][0]
        assert "is_stub: False" in cypher
        assert "doi:" in cypher

    def test_finds_by_arxiv_id(self):
        repo, client = _make_repo([{"pid": "real456"}])
        assert repo.find_real_paper_id(arxiv_id="1706.03762") == "real456"

    def test_returns_none_when_not_found(self):
        repo, client = _make_repo([])
        assert repo.find_real_paper_id(arxiv_id="1706.03762") is None

    def test_returns_none_when_neither_identifier_given(self):
        repo, client = _make_repo([{"pid": "real123"}])
        assert repo.find_real_paper_id() is None
        client.query.assert_not_called()


class TestGetCitationGraph:
    def test_returns_all_papers_and_cites_edges(self):
        client = MagicMock()
        client.query.side_effect = [
            [
                {"paper_id": "p1", "title": "Paper One", "name": "Paper One", "year": 2021, "is_stub": False},
                {"paper_id": "p2", "title": "Paper Two", "name": "Paper Two", "year": 2022, "is_stub": False},
                {"paper_id": "arxiv_1706.03762", "title": None, "name": "Attention Is All You Need", "year": None, "is_stub": True},
            ],
            [
                {"source": "p2", "target": "p1"},
                {"source": "p1", "target": "arxiv_1706.03762"},
            ],
        ]
        repo = GraphRepository(client)

        graph = repo.get_citation_graph()

        assert len(graph["papers"]) == 3
        assert len(graph["edges"]) == 2
        assert graph["edges"][0] == {"source": "p2", "target": "p1"}
        stub = next(p for p in graph["papers"] if p["paper_id"] == "arxiv_1706.03762")
        assert stub["is_stub"] is True

    def test_empty_graph(self):
        client = MagicMock()
        client.query.side_effect = [[], []]
        repo = GraphRepository(client)

        graph = repo.get_citation_graph()

        assert graph == {"papers": [], "edges": []}
