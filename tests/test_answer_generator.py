"""Tests for AnswerGenerator (Phase 15.2)."""

from unittest.mock import MagicMock

import pytest

from app.llm.answer_generator import AnswerGenerator, NO_CONTEXT_ANSWER, SYSTEM_PROMPT


def _make_generator(llm_response="Generated answer."):
    llm_client = MagicMock()
    llm_client.generate_response.return_value = llm_response
    generator = AnswerGenerator(llm_client=llm_client)
    return generator, llm_client


GROUNDED_RETRIEVAL = {
    "query": "How does GPT compare to RNN?",
    "query_type": "COMPARISON",
    "graph_facts": [{
        "subject": {"name": "GPT", "type": "Method", "paper_id": "p2"},
        "relation": "IMPROVES_UPON",
        "object": {"name": "RNN", "type": "Method", "paper_id": None},
        "evidence": "GPT outperforms RNN.",
        "source_paper_ids": ["p2"],
    }],
    "vector_results": [{
        "id": "p2__chunk_0", "score": 0.9, "text": "GPT is a language model.",
        "paper_id": "p2", "section": "Abstract", "node_type": "Method",
        "node_name": "GPT", "source_text": "GPT is a language model.", "page": 1,
    }],
    "citation_paths": [],
    "source_paper_ids": ["p2"],
}

EMPTY_RETRIEVAL = {
    "query": "What is quantum gravity?",
    "query_type": "EXPLANATION",
    "graph_facts": [],
    "vector_results": [],
    "citation_paths": [],
    "source_paper_ids": [],
}


class TestGenerateWithContext:
    def test_calls_llm_with_system_prompt_and_returns_answer(self):
        generator, llm_client = _make_generator("GPT improves on RNN [p2].")

        result = generator.generate("How does GPT compare to RNN?", GROUNDED_RETRIEVAL)

        llm_client.generate_response.assert_called_once()
        call_args, call_kwargs = llm_client.generate_response.call_args
        assert call_kwargs["system_prompt"] == SYSTEM_PROMPT
        assert "GPT" in call_args[0]
        assert result["answer"] == "GPT improves on RNN [p2]."

    def test_includes_sources_from_context(self):
        generator, _ = _make_generator()
        result = generator.generate("q", GROUNDED_RETRIEVAL)
        assert result["sources"] == [{"paper_id": "p2", "title": None}]

    def test_includes_graph_facts_used(self):
        generator, _ = _make_generator()
        result = generator.generate("q", GROUNDED_RETRIEVAL)
        assert result["graph_facts_used"] == GROUNDED_RETRIEVAL["graph_facts"]

    def test_no_confidence_notes_when_well_grounded(self):
        generator, _ = _make_generator()
        result = generator.generate("q", GROUNDED_RETRIEVAL)
        assert result["confidence_notes"] == []


class TestGenerateWithoutContext:
    def test_refuses_to_answer_without_calling_llm(self):
        generator, llm_client = _make_generator()

        result = generator.generate("What is quantum gravity?", EMPTY_RETRIEVAL)

        llm_client.generate_response.assert_not_called()
        assert result["answer"] == NO_CONTEXT_ANSWER
        assert result["sources"] == []
        assert result["graph_facts_used"] == []

    def test_confidence_notes_flag_missing_context(self):
        generator, _ = _make_generator()
        result = generator.generate("q", EMPTY_RETRIEVAL)
        assert len(result["confidence_notes"]) >= 1
        assert any("No source papers" in n for n in result["confidence_notes"])


class TestPartialGrounding:
    def test_graph_facts_without_paper_ids_flags_no_sources(self):
        """Facts that don't resolve to any paper_id still produce context text,
        but confidence_notes should flag the missing source grounding."""
        generator, llm_client = _make_generator()
        retrieval = {
            "graph_facts": [{
                "subject": {"name": "X", "type": "Method", "paper_id": None},
                "relation": "RELATED_TO",
                "object": {"name": "Y", "type": "Method", "paper_id": None},
                "evidence": None,
                "source_paper_ids": [],
            }],
            "vector_results": [],
            "citation_paths": [],
        }

        result = generator.generate("q", retrieval)

        llm_client.generate_response.assert_called_once()
        assert result["sources"] == []
        assert any("No source papers" in n for n in result["confidence_notes"])


class TestDefaultLlmClient:
    def test_uses_real_llm_client_when_none_given(self, monkeypatch):
        import app.services.llm as llm_module

        fake_client = MagicMock()
        fake_client.generate_response.return_value = "ok"
        monkeypatch.setattr(llm_module, "llm_client", fake_client)

        generator = AnswerGenerator()
        result = generator.generate("q", GROUNDED_RETRIEVAL)

        fake_client.generate_response.assert_called_once()
        assert result["answer"] == "ok"
