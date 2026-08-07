"""Tests for ContextBuilder (Phase 15.1)."""

import pytest

from app.llm.context_builder import ContextBuilder


@pytest.fixture
def builder():
    return ContextBuilder()


GRAPH_FACT = {
    "subject": {"name": "GPT", "type": "Method", "paper_id": "p2"},
    "relation": "IMPROVES_UPON",
    "object": {"name": "RNN", "type": "Method", "paper_id": None},
    "evidence": "GPT outperforms RNN on language modeling.",
    "source_paper_ids": ["p2"],
}

VECTOR_RESULT = {
    "id": "p1__chunk_0",
    "score": 0.9,
    "text": "The Transformer relies on self-attention.",
    "paper_id": "p1",
    "section": "Abstract",
    "node_type": "Method",
    "node_name": "Transformer",
    "source_text": "The Transformer relies on self-attention.",
    "page": 1,
}

CITATION_PATH = {
    "paper_id": "p0",
    "title": "Earlier Paper",
    "depth": 1,
    "path": ["p1", "p0"],
    "direction": "forward",
}


class TestBuild:
    def test_empty_retrieval_produces_empty_context(self, builder):
        result = builder.build({"graph_facts": [], "vector_results": [], "citation_paths": []})
        assert result["context_text"] == ""
        assert result["source_papers"] == []

    def test_missing_keys_do_not_raise(self, builder):
        result = builder.build({})
        assert result["context_text"] == ""

    def test_graph_facts_rendered_with_evidence_and_source(self, builder):
        result = builder.build({"graph_facts": [GRAPH_FACT], "vector_results": [], "citation_paths": []})
        assert "Graph facts:" in result["context_text"]
        assert "GPT IMPROVES_UPON RNN" in result["context_text"]
        assert "GPT outperforms RNN on language modeling." in result["context_text"]
        assert "p2" in result["context_text"]

    def test_text_evidence_rendered_with_location(self, builder):
        result = builder.build({"graph_facts": [], "vector_results": [VECTOR_RESULT], "citation_paths": []})
        assert "Text evidence:" in result["context_text"]
        assert "p1 / Abstract" in result["context_text"]
        assert "The Transformer relies on self-attention." in result["context_text"]

    def test_citation_paths_rendered(self, builder):
        result = builder.build({"graph_facts": [], "vector_results": [], "citation_paths": [CITATION_PATH]})
        assert "Citation paths:" in result["context_text"]
        assert "p1 -> p0" in result["context_text"]

    def test_source_papers_deduplicated_across_sections(self, builder):
        result = builder.build({
            "graph_facts": [GRAPH_FACT],
            "vector_results": [VECTOR_RESULT],
            "citation_paths": [CITATION_PATH],
        })
        ids = [p["paper_id"] for p in result["source_papers"]]
        assert ids == sorted(set(ids))
        assert "p0" in ids and "p1" in ids and "p2" in ids

    def test_source_papers_pick_up_titles_from_paper_nodes(self, builder):
        fact = {
            "subject": {"name": "Some Paper", "type": "Paper", "paper_id": "p9"},
            "relation": "CITES",
            "object": {"name": "Other Paper", "type": "Paper", "paper_id": "p10"},
            "evidence": None,
            "source_paper_ids": ["p9"],
        }
        result = builder.build({"graph_facts": [fact], "vector_results": [], "citation_paths": []})
        titles = {p["paper_id"]: p["title"] for p in result["source_papers"]}
        assert titles["p9"] == "Some Paper"
        assert titles["p10"] == "Other Paper"

    def test_full_context_includes_all_sections_in_order(self, builder):
        result = builder.build({
            "graph_facts": [GRAPH_FACT],
            "vector_results": [VECTOR_RESULT],
            "citation_paths": [CITATION_PATH],
        })
        text = result["context_text"]
        assert text.index("Graph facts:") < text.index("Text evidence:")
        assert text.index("Text evidence:") < text.index("Citation paths:")
        assert text.index("Citation paths:") < text.index("Source papers:")
