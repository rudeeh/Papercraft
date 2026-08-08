"""Tests for app/graph/llm_extractor.py."""

import json

import pytest

from app.graph.llm_extractor import (
    LLMEntityRelationExtractor,
    _evidence_is_grounded,
    _parse_json_array,
)
from app.services.llm import LLMNotConfiguredError

PAPER_TEXT = (
    "We propose the Transformer, a model architecture eschewing recurrence. "
    "The Transformer is evaluated on the WMT14 English-to-German translation task "
    "and achieves a BLEU score of 28.4, improving on previous best results."
)

TRANSFORMER_EVIDENCE = "We propose the Transformer, a model architecture eschewing recurrence."
WMT14_EVIDENCE = (
    "The Transformer is evaluated on the WMT14 English-to-German translation task"
)


class FakeLLM:
    """Stands in for LLMClient, returning canned responses per call."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def generate_response(self, prompt, system_prompt=None, api_key=None):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if not self._responses:
            return "[]"
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def entity_json(**overrides):
    payload = {
        "name": "Transformer",
        "type": "Method",
        "source_section": "Abstract",
        "evidence": TRANSFORMER_EVIDENCE,
        "confidence": 0.9,
    }
    payload.update(overrides)
    return json.dumps([payload])


class TestParseJsonArray:
    def test_plain_array(self):
        assert _parse_json_array('[{"a": 1}]') == [{"a": 1}]

    def test_fenced_block(self):
        assert _parse_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_prose_wrapped_array(self):
        assert _parse_json_array('Here is the JSON:\n[{"a": 1}]\nHope that helps!') == [{"a": 1}]

    def test_bare_object_is_wrapped(self):
        assert _parse_json_array('{"a": 1}') == [{"a": 1}]

    @pytest.mark.parametrize("junk", ["", "I cannot help with that.", "[unclosed", None])
    def test_unparseable_returns_empty(self, junk):
        assert _parse_json_array(junk) == []


class TestEvidenceGrounding:
    def test_verbatim_quote_is_grounded(self):
        assert _evidence_is_grounded(TRANSFORMER_EVIDENCE, PAPER_TEXT)

    def test_whitespace_and_case_differences_tolerated(self):
        noisy = "we  propose   the transformer,\na model architecture eschewing recurrence."
        assert _evidence_is_grounded(noisy, PAPER_TEXT)

    def test_invented_quote_is_not_grounded(self):
        assert not _evidence_is_grounded(
            "The Transformer was trained on 500 billion tokens of proprietary data.", PAPER_TEXT
        )

    def test_very_short_evidence_is_rejected(self):
        # Short strings match by accident; "model" is in almost any paper.
        assert not _evidence_is_grounded("model", PAPER_TEXT)


class TestEntityExtraction:
    def test_extracts_a_well_formed_entity(self):
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM(entity_json()))
        entities = extractor.extract_entities(PAPER_TEXT)
        assert len(entities) == 1
        assert entities[0]["name"] == "Transformer"
        assert entities[0]["confidence"] == 0.9

    def test_stamps_the_model_version(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(entity_json()), model_version="extraction-v9"
        )
        assert extractor.extract_entities(PAPER_TEXT)[0]["extracted_by"] == "extraction-v9"

    def test_rejects_a_type_outside_the_ontology(self):
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM(entity_json(type="Vibe")))
        assert extractor.extract_entities(PAPER_TEXT) == []

    def test_rejects_a_valid_node_type_that_is_not_extractable(self):
        # Paper/Author/Section nodes come from the parser, not from this
        # extractor; accepting them here would create rogue Paper nodes.
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM(entity_json(type="Paper")))
        assert extractor.extract_entities(PAPER_TEXT) == []

    def test_rejects_hallucinated_evidence(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(entity_json(evidence="The Transformer cures scurvy, we found."))
        )
        assert extractor.extract_entities(PAPER_TEXT) == []

    @pytest.mark.parametrize("missing", ["name", "type", "evidence"])
    def test_rejects_entities_missing_a_required_field(self, missing):
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM(entity_json(**{missing: ""})))
        assert extractor.extract_entities(PAPER_TEXT) == []

    def test_non_dict_array_elements_are_skipped(self):
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM('["just a string", 42, null]'))
        assert extractor.extract_entities(PAPER_TEXT) == []

    def test_empty_text_makes_no_llm_call(self):
        fake = FakeLLM(entity_json())
        assert LLMEntityRelationExtractor(llm_client=fake).extract_entities("  ") == []
        assert fake.calls == []

    def test_missing_api_key_returns_empty_not_an_error(self):
        fake = FakeLLM(LLMNotConfiguredError("no key"))
        assert LLMEntityRelationExtractor(llm_client=fake).extract_entities(PAPER_TEXT) == []

    def test_llm_exception_returns_empty(self):
        fake = FakeLLM(RuntimeError("upstream 500"))
        assert LLMEntityRelationExtractor(llm_client=fake).extract_entities(PAPER_TEXT) == []

    def test_prompt_is_truncated_to_the_cap(self):
        fake = FakeLLM("[]")
        extractor = LLMEntityRelationExtractor(llm_client=fake, max_prompt_chars=50)
        extractor.extract_entities("x" * 5000)
        assert len(fake.calls[0]["prompt"]) < 500


class TestRelationExtraction:
    ENTITIES = [
        {"name": "Transformer", "type": "Method"},
        {"name": "WMT14", "type": "Dataset"},
    ]

    def _relation_json(self, **overrides):
        payload = {
            "source": "Transformer",
            "source_type": "Method",
            "relation": "USES_DATASET",
            "target": "WMT14",
            "target_type": "Dataset",
            "evidence": WMT14_EVIDENCE,
            "confidence": 0.8,
        }
        payload.update(overrides)
        return json.dumps([payload])

    def test_extracts_a_valid_relation(self):
        extractor = LLMEntityRelationExtractor(llm_client=FakeLLM(self._relation_json()))
        relations = extractor.extract_relations(self.ENTITIES, PAPER_TEXT)
        assert len(relations) == 1
        assert relations[0]["relation"] == "USES_DATASET"

    def test_types_come_from_stage_one_not_the_models_restatement(self):
        # The model contradicts itself about the target's type; stage 1 wins.
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(target_type="Task"))
        )
        relations = extractor.extract_relations(self.ENTITIES, PAPER_TEXT)
        assert relations[0]["target_type"] == "Dataset"

    def test_rejects_an_edge_the_ontology_forbids(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(relation="WRITTEN_BY"))
        )
        assert extractor.extract_relations(self.ENTITIES, PAPER_TEXT) == []

    def test_rejects_an_invented_relation_type(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(relation="VIBES_WITH"))
        )
        assert extractor.extract_relations(self.ENTITIES, PAPER_TEXT) == []

    def test_rejects_endpoints_not_in_the_entity_list(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(target="ImageNet"))
        )
        assert extractor.extract_relations(self.ENTITIES, PAPER_TEXT) == []

    def test_rejects_self_relations(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(target="Transformer"))
        )
        assert extractor.extract_relations(self.ENTITIES, PAPER_TEXT) == []

    def test_rejects_hallucinated_evidence(self):
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(self._relation_json(evidence="We ran it on a dataset we made up."))
        )
        assert extractor.extract_relations(self.ENTITIES, PAPER_TEXT) == []

    def test_fewer_than_two_entities_makes_no_llm_call(self):
        fake = FakeLLM(self._relation_json())
        extractor = LLMEntityRelationExtractor(llm_client=fake)
        assert extractor.extract_relations([self.ENTITIES[0]], PAPER_TEXT) == []
        assert fake.calls == []

    def test_allowed_relations_are_listed_in_the_prompt(self):
        fake = FakeLLM("[]")
        LLMEntityRelationExtractor(llm_client=fake).extract_relations(self.ENTITIES, PAPER_TEXT)
        assert "USES_DATASET" in fake.calls[0]["prompt"]


class TestTwoStageExtract:
    def test_relations_are_extracted_over_stage_one_entities(self):
        entities_response = json.dumps([
            {
                "name": "Transformer",
                "type": "Method",
                "evidence": TRANSFORMER_EVIDENCE,
                "confidence": 0.9,
            },
            {"name": "WMT14", "type": "Dataset", "evidence": WMT14_EVIDENCE, "confidence": 0.9},
        ])
        relations_response = json.dumps([
            {
                "source": "Transformer",
                "relation": "USES_DATASET",
                "target": "WMT14",
                "evidence": WMT14_EVIDENCE,
                "confidence": 0.85,
            }
        ])
        extractor = LLMEntityRelationExtractor(
            llm_client=FakeLLM(entities_response, relations_response)
        )
        result = extractor.extract(PAPER_TEXT)
        assert len(result["entities"]) == 2
        assert len(result["relations"]) == 1

    def test_no_entities_means_no_relation_call(self):
        fake = FakeLLM("[]")
        result = LLMEntityRelationExtractor(llm_client=fake).extract(PAPER_TEXT)
        assert result == {"entities": [], "relations": []}
        assert len(fake.calls) == 1  # entity stage only
