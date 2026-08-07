"""Tests for GraphRetriever (Phase 11)."""

from unittest.mock import MagicMock

import pytest

from app.retrieval.graph_retriever import GraphRetriever


def _make_retriever(rows):
    client = MagicMock()
    client.query.return_value = rows
    return GraphRetriever(client), client


class TestFindCitingPapers:
    def test_returns_facts_with_evidence_and_source_paper_ids(self):
        retriever, client = _make_retriever([
            {"citer_id": "paper_a", "citer_title": "Paper A", "evidence": "Referenced as [3]",
             "cited_title": "Paper B"},
        ])

        facts = retriever.find_citing_papers("paper_b")

        assert len(facts) == 1
        fact = facts[0]
        assert fact["subject"] == {"name": "Paper A", "type": "Paper", "paper_id": "paper_a"}
        assert fact["relation"] == "CITES"
        assert fact["object"]["paper_id"] == "paper_b"
        assert fact["evidence"] == "Referenced as [3]"
        assert fact["source_paper_ids"] == ["paper_a", "paper_b"]

    def test_empty_result(self):
        retriever, client = _make_retriever([])
        assert retriever.find_citing_papers("paper_x") == []
        client.query.assert_called_once()


class TestFindCitedPapers:
    def test_forward_direction(self):
        retriever, client = _make_retriever([
            {"cited_id": "paper_c", "cited_title": "Paper C", "evidence": None},
        ])

        facts = retriever.find_cited_papers("paper_a")

        assert facts[0]["subject"]["paper_id"] == "paper_a"
        assert facts[0]["object"] == {"name": "Paper C", "type": "Paper", "paper_id": "paper_c"}
        assert facts[0]["evidence"] is None
        assert facts[0]["source_paper_ids"] == ["paper_a"]


class TestFindPapersUsingDataset:
    def test_returns_paper_facts(self):
        retriever, client = _make_retriever([
            {"paper_id": "p1", "title": "Paper One", "evidence": "trained on WMT14", "relation": "MENTIONS"},
        ])

        facts = retriever.find_papers_using_dataset("WMT14")

        assert facts[0]["subject"] == {"name": "Paper One", "type": "Paper", "paper_id": "p1"}
        assert facts[0]["object"]["name"] == "WMT14"
        assert facts[0]["object"]["type"] == "Dataset"
        assert facts[0]["relation"] == "MENTIONS"
        assert facts[0]["evidence"] == "trained on WMT14"


class TestFindMethodsEvaluatedOn:
    def test_returns_method_facts(self):
        retriever, client = _make_retriever([
            {"method_name": "Transformer", "paper_id": "p1", "evidence": "evaluated on WMT14",
             "relation": "USES_DATASET"},
        ])

        facts = retriever.find_methods_evaluated_on("WMT14")

        assert facts[0]["subject"] == {"name": "Transformer", "type": "Method", "paper_id": "p1"}
        assert facts[0]["object"]["name"] == "WMT14"
        assert facts[0]["evidence"] == "evaluated on WMT14"
        assert facts[0]["source_paper_ids"] == ["p1"]


class TestFindMethodsImprovingUpon:
    def test_returns_improvement_facts(self):
        retriever, client = _make_retriever([
            {"improver_name": "GPT", "paper_id": "p2", "evidence": "GPT outperforms RNN"},
        ])

        facts = retriever.find_methods_improving_upon("RNN")

        assert facts[0]["subject"] == {"name": "GPT", "type": "Method", "paper_id": "p2"}
        assert facts[0]["relation"] == "IMPROVES_UPON"
        assert facts[0]["object"]["name"] == "RNN"
        assert facts[0]["evidence"] == "GPT outperforms RNN"


class TestFindEntitiesForPaper:
    def test_returns_entity_facts_with_node_labels(self):
        retriever, client = _make_retriever([
            {"labels": ["Method"], "name": "Transformer", "evidence": "The Transformer is..."},
            {"labels": ["Dataset"], "name": "WMT14", "evidence": None},
        ])

        facts = retriever.find_entities_for_paper("paper_a")

        assert len(facts) == 2
        assert facts[0]["subject"]["paper_id"] == "paper_a"
        assert facts[0]["object"] == {"name": "Transformer", "type": "Method", "paper_id": "paper_a"}
        assert facts[0]["relation"] == "MENTIONS"
        assert facts[0]["evidence"] == "The Transformer is..."
        assert facts[1]["object"]["type"] == "Dataset"

    def test_missing_labels_defaults_to_entity(self):
        retriever, client = _make_retriever([
            {"labels": [], "name": "Unknown", "evidence": None},
        ])
        facts = retriever.find_entities_for_paper("paper_a")
        assert facts[0]["object"]["type"] == "Entity"


class TestGetEntityRelations:
    def test_returns_relations_in_either_direction(self):
        retriever, client = _make_retriever([
            {"relation": "IMPROVES_UPON", "source_name": "GPT", "source_labels": ["Method"],
             "target_name": "RNN", "target_labels": ["Method"], "evidence": "GPT beats RNN",
             "paper_id": "p2"},
        ])

        facts = retriever.get_entity_relations("RNN", "Method")

        assert facts[0]["subject"]["name"] == "GPT"
        assert facts[0]["object"]["name"] == "RNN"
        assert facts[0]["relation"] == "IMPROVES_UPON"
        assert facts[0]["source_paper_ids"] == ["p2"]

    def test_uses_correct_label_in_cypher(self):
        retriever, client = _make_retriever([])
        retriever.get_entity_relations("BERT", "Method")
        cypher = client.query.call_args[0][0]
        assert "MATCH (e:Method {name: $name})" in cypher
