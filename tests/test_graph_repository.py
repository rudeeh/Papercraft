"""Tests for GraphRepository (Phase 8.2), focused on get_paper_graph."""

from unittest.mock import MagicMock

import pytest

from app.storage.graph_repository import GraphRepository


def _make_repo(rows):
    client = MagicMock()
    client.query.return_value = rows
    return GraphRepository(client), client


class TestGetPaperGraph:
    def test_returns_empty_dict_when_paper_missing(self):
        repo, client = _make_repo([])
        assert repo.get_paper_graph("missing") == {}

    def test_returns_empty_dict_when_paper_node_is_null(self):
        repo, client = _make_repo([{"p": None}])
        assert repo.get_paper_graph("missing") == {}

    def test_aggregates_all_relationships_not_just_first_row(self):
        rows = [
            {
                "p": {"paper_id": "p1", "title": "My Paper"}, "p_labels": ["Paper"],
                "r_type": "HAS_SECTION", "r_props": {},
                "r_start": {"paper_id": "p1", "title": "My Paper"}, "r_start_labels": ["Paper"],
                "r_end": {"section_id": "sec1", "heading": "Intro"}, "r_end_labels": ["Section"],
            },
            {
                "p": {"paper_id": "p1", "title": "My Paper"}, "p_labels": ["Paper"],
                "r_type": "MENTIONS", "r_props": {"confidence": 1.0},
                "r_start": {"paper_id": "p1", "title": "My Paper"}, "r_start_labels": ["Paper"],
                "r_end": {"name": "Transformer", "evidence": "..."}, "r_end_labels": ["Method"],
            },
        ]
        repo, client = _make_repo(rows)

        graph = repo.get_paper_graph("p1")

        node_ids = {n["id"] for n in graph["nodes"]}
        assert node_ids == {"p1", "sec1", "Transformer"}
        assert len(graph["edges"]) == 2

        edge_types = {e["type"] for e in graph["edges"]}
        assert edge_types == {"HAS_SECTION", "MENTIONS"}

        mentions_edge = next(e for e in graph["edges"] if e["type"] == "MENTIONS")
        assert mentions_edge["source"] == "p1"
        assert mentions_edge["source_type"] == "Paper"
        assert mentions_edge["target"] == "Transformer"
        assert mentions_edge["target_type"] == "Method"

    def test_paper_with_no_relationships_returns_lone_node(self):
        rows = [{
            "p": {"paper_id": "p1", "title": "Lonely Paper"}, "p_labels": ["Paper"],
            "r_type": None, "r_props": None,
            "r_start": None, "r_start_labels": None,
            "r_end": None, "r_end_labels": None,
        }]
        repo, client = _make_repo(rows)

        graph = repo.get_paper_graph("p1")

        assert [n["id"] for n in graph["nodes"]] == ["p1"]
        assert graph["edges"] == []

    def test_nodes_deduplicated_across_multiple_edges(self):
        # Two different sections both linked to the paper -- paper node
        # must only appear once in the result.
        rows = [
            {
                "p": {"paper_id": "p1"}, "p_labels": ["Paper"],
                "r_type": "HAS_SECTION", "r_props": {},
                "r_start": {"paper_id": "p1"}, "r_start_labels": ["Paper"],
                "r_end": {"section_id": "sec1", "heading": "Intro"}, "r_end_labels": ["Section"],
            },
            {
                "p": {"paper_id": "p1"}, "p_labels": ["Paper"],
                "r_type": "HAS_SECTION", "r_props": {},
                "r_start": {"paper_id": "p1"}, "r_start_labels": ["Paper"],
                "r_end": {"section_id": "sec2", "heading": "Methods"}, "r_end_labels": ["Section"],
            },
        ]
        repo, client = _make_repo(rows)

        graph = repo.get_paper_graph("p1")

        assert sorted(n["id"] for n in graph["nodes"]) == ["p1", "sec1", "sec2"]
