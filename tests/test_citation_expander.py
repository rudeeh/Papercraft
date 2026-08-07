"""Tests for CitationExpander (Phase 14)."""

from unittest.mock import MagicMock

import pytest

from app.retrieval.citation_expander import CitationExpander


def _make_client(forward_edges=None, backward_edges=None):
    """
    forward_edges: pid -> list of {"pid", "title"} rows for papers *pid cites*.
    backward_edges: pid -> list of {"pid", "title"} rows for papers *citing pid*.
    """
    forward_edges = forward_edges or {}
    backward_edges = backward_edges or {}

    def side_effect(cypher, params):
        pid = params["pid"]
        if "cited:Paper" in cypher:
            return forward_edges.get(pid, [])
        return backward_edges.get(pid, [])

    client = MagicMock()
    client.query.side_effect = side_effect
    return client


class TestForwardExpansion:
    def test_walks_forward_chain_up_to_depth(self):
        client = _make_client(forward_edges={
            "A": [{"pid": "B", "title": "Paper B"}],
            "B": [{"pid": "C", "title": "Paper C"}],
            "C": [{"pid": "D", "title": "Paper D"}],
        })
        expander = CitationExpander(client)

        results = expander.expand("A", direction="forward", max_depth=2)

        assert [r["paper_id"] for r in results] == ["B", "C"]
        assert results[0]["depth"] == 1
        assert results[1]["depth"] == 2
        assert results[1]["path"] == ["A", "B", "C"]
        assert all(r["direction"] == "forward" for r in results)

    def test_depth_limit_stops_traversal(self):
        client = _make_client(forward_edges={
            "A": [{"pid": "B", "title": "Paper B"}],
            "B": [{"pid": "C", "title": "Paper C"}],
        })
        expander = CitationExpander(client)

        results = expander.expand("A", direction="forward", max_depth=1)

        assert [r["paper_id"] for r in results] == ["B"]

    def test_hard_depth_cap_at_five(self):
        # Chain of 6 forward hops; requesting max_depth=10 must still stop at 5.
        chain = ["A", "B", "C", "D", "E", "F", "G"]
        forward_edges = {
            chain[i]: [{"pid": chain[i + 1], "title": chain[i + 1]}]
            for i in range(len(chain) - 1)
        }
        client = _make_client(forward_edges=forward_edges)
        expander = CitationExpander(client)

        results = expander.expand("A", direction="forward", max_depth=10)

        assert [r["paper_id"] for r in results] == ["B", "C", "D", "E", "F"]
        assert "G" not in [r["paper_id"] for r in results]


class TestMaxPapersLimit:
    def test_caps_total_results(self):
        client = _make_client(forward_edges={
            "A": [{"pid": "B", "title": "B"}, {"pid": "C", "title": "C"}],
        })
        expander = CitationExpander(client)

        results = expander.expand("A", direction="forward", max_depth=2, max_papers=1)

        assert len(results) == 1


class TestCycleSafety:
    def test_does_not_loop_on_citation_cycle(self):
        # X cites Y, Y cites X -- a real cycle.
        client = _make_client(forward_edges={
            "X": [{"pid": "Y", "title": "Y"}],
            "Y": [{"pid": "X", "title": "X"}],
        })
        expander = CitationExpander(client)

        results = expander.expand("X", direction="forward", max_depth=5)

        # Terminates and never re-adds the start node.
        assert [r["paper_id"] for r in results] == ["Y"]


class TestBackwardAndBothDirections:
    def test_backward_direction(self):
        client = _make_client(backward_edges={
            "P": [{"pid": "Q", "title": "Q cites P"}],
        })
        expander = CitationExpander(client)

        results = expander.expand("P", direction="backward", max_depth=1)

        assert [r["paper_id"] for r in results] == ["Q"]
        assert results[0]["direction"] == "backward"

    def test_both_directions_combined(self):
        client = _make_client(
            forward_edges={"A": [{"pid": "B", "title": "B"}]},
            backward_edges={"A": [{"pid": "Z", "title": "Z"}]},
        )
        expander = CitationExpander(client)

        results = expander.expand("A", direction="both", max_depth=1)

        ids = {r["paper_id"] for r in results}
        assert ids == {"B", "Z"}


class TestExpandPaperIds:
    def test_returns_flat_paper_id_list(self):
        client = _make_client(forward_edges={
            "A": [{"pid": "B", "title": "B"}],
        })
        expander = CitationExpander(client)

        ids = expander.expand_paper_ids("A", direction="forward", max_depth=1)

        assert ids == ["B"]
