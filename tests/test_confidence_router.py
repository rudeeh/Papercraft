"""Tests for app/graph/confidence_router.py."""

import pytest

from app.db.models import DraftKind, RoutingDecision
from app.graph.confidence_router import (
    AGREEMENT_BONUS,
    HEURISTIC_BASE_CONFIDENCE,
    ConfidenceRouter,
    entity_key,
    relation_key,
)

LONG_EVIDENCE = (
    "The Transformer is the first transduction model relying entirely on "
    "self-attention to compute representations of its input and output."
)
SHORT_EVIDENCE = "We use BERT."


def entity(name="Transformer", etype="Method", evidence=LONG_EVIDENCE, **extra):
    return {"name": name, "type": etype, "evidence": evidence, **extra}


def relation(source="Transformer", target="WMT14", rel="USES_DATASET", evidence=LONG_EVIDENCE, **extra):
    return {
        "source": source,
        "source_type": "Method",
        "relation": rel,
        "target": target,
        "target_type": "Dataset",
        "evidence": evidence,
        **extra,
    }


class TestScoring:
    def test_missing_confidence_defaults_to_the_heuristic_baseline(self):
        assert ConfidenceRouter().score(entity()) == HEURISTIC_BASE_CONFIDENCE

    def test_unparseable_confidence_falls_back_to_the_baseline(self):
        assert ConfidenceRouter().score(entity(confidence="high")) == HEURISTIC_BASE_CONFIDENCE

    def test_no_evidence_scores_zero(self):
        # The ontology requires evidence; without it there is nothing for a
        # curator to check, so it cannot be promoted at any claimed score.
        assert ConfidenceRouter().score(entity(evidence="", confidence=0.99)) == 0.0

    def test_whitespace_only_evidence_scores_zero(self):
        assert ConfidenceRouter().score(entity(evidence="   \n ", confidence=0.99)) == 0.0

    def test_thin_evidence_is_penalised(self):
        router = ConfidenceRouter()
        assert router.score(entity(evidence=SHORT_EVIDENCE, confidence=0.9)) < 0.9

    def test_agreement_raises_confidence(self):
        router = ConfidenceRouter()
        base = router.score(entity(confidence=0.6))
        assert router.score(entity(confidence=0.6), agreed=True) == pytest.approx(
            base + AGREEMENT_BONUS
        )

    def test_score_is_clamped_to_unit_interval(self):
        router = ConfidenceRouter()
        assert router.score(entity(confidence=5.0), agreed=True) == 1.0
        assert router.score(entity(confidence=-3.0)) == 0.0


class TestDecisions:
    @pytest.mark.parametrize(
        "confidence,expected",
        [
            (1.0, RoutingDecision.AUTO_INSERT),
            (0.85, RoutingDecision.AUTO_INSERT),
            (0.84, RoutingDecision.DRAFT),
            (0.50, RoutingDecision.DRAFT),
            (0.49, RoutingDecision.MANUAL),
            (0.01, RoutingDecision.MANUAL),
            (0.0, RoutingDecision.DISCARDED),
        ],
    )
    def test_thresholds_are_inclusive_at_the_boundary(self, confidence, expected):
        router = ConfidenceRouter(auto_insert_threshold=0.85, draft_threshold=0.50)
        assert router.decide(confidence) is expected

    def test_inverted_thresholds_are_rejected_at_construction(self):
        # Silently accepting these would make DRAFT unreachable.
        with pytest.raises(ValueError, match="CONFIDENCE_DRAFT"):
            ConfidenceRouter(auto_insert_threshold=0.4, draft_threshold=0.9)

    def test_heuristic_extractions_auto_insert_by_default(self):
        """
        The pre-routing behaviour: a heuristic-only deployment's graph must
        not change just because this module exists.
        """
        router = ConfidenceRouter()
        routed = router.route_one(entity(), DraftKind.ENTITY)
        assert routed.is_auto_insert
        assert not routed.needs_review


class TestRouteOne:
    def test_carries_kind_and_payload_through(self):
        item = entity()
        routed = ConfidenceRouter().route_one(item, DraftKind.ENTITY)
        assert routed.kind is DraftKind.ENTITY
        assert routed.payload is item

    def test_uses_the_items_own_extractor_id_when_present(self):
        routed = ConfidenceRouter().route_one(
            entity(extracted_by="extraction-v9"), DraftKind.ENTITY
        )
        assert routed.extracted_by == "extraction-v9"

    def test_falls_back_to_the_configured_model_version(self):
        routed = ConfidenceRouter(default_extracted_by="fallback-v1").route_one(
            entity(), DraftKind.ENTITY
        )
        assert routed.extracted_by == "fallback-v1"

    def test_to_dict_is_json_serialisable_shaped(self):
        d = ConfidenceRouter().route_one(entity(), DraftKind.ENTITY).to_dict()
        assert d["kind"] == "entity"
        assert isinstance(d["routing"], str)


class TestMergeAndRoute:
    def test_items_found_by_both_extractors_appear_once(self):
        router = ConfidenceRouter()
        entities, _ = router.merge_and_route([entity()], [entity(confidence=0.7)], [], [])
        assert len(entities) == 1

    def test_agreement_lifts_a_medium_llm_score_to_auto_insert(self):
        router = ConfidenceRouter(auto_insert_threshold=0.85, draft_threshold=0.5)
        entities, _ = router.merge_and_route([entity()], [entity(confidence=0.75)], [], [])
        assert entities[0].is_auto_insert

    def test_llm_only_medium_confidence_is_queued_not_inserted(self):
        router = ConfidenceRouter(auto_insert_threshold=0.85, draft_threshold=0.5)
        entities, _ = router.merge_and_route([], [entity(confidence=0.6)], [], [])
        assert entities[0].needs_review

    def test_llm_version_wins_on_collision(self):
        router = ConfidenceRouter()
        entities, _ = router.merge_and_route(
            [entity(evidence="heuristic window")],
            [entity(evidence=LONG_EVIDENCE, confidence=0.8)],
            [],
            [],
        )
        assert entities[0].payload["evidence"] == LONG_EVIDENCE

    def test_matching_is_case_insensitive_on_names(self):
        router = ConfidenceRouter()
        entities, _ = router.merge_and_route(
            [entity(name="transformer")], [entity(name="Transformer", confidence=0.7)], [], []
        )
        assert len(entities) == 1

    def test_different_types_of_the_same_name_stay_separate(self):
        router = ConfidenceRouter()
        entities, _ = router.merge_and_route(
            [entity(name="GLUE", etype="Dataset")],
            [entity(name="GLUE", etype="Task", confidence=0.7)],
            [],
            [],
        )
        assert len(entities) == 2

    def test_relations_are_routed_independently_of_entities(self):
        router = ConfidenceRouter()
        entities, relations = router.merge_and_route([], [], [relation()], [relation(confidence=0.9)])
        assert entities == []
        assert len(relations) == 1
        assert relations[0].kind is DraftKind.RELATION

    def test_empty_input_produces_empty_output(self):
        assert ConfidenceRouter().merge_and_route([], [], [], []) == ([], [])


class TestKeys:
    def test_entity_key_normalises_case_and_whitespace(self):
        assert entity_key({"name": "  Transformer ", "type": "Method"}) == ("transformer", "Method")

    def test_relation_key_uses_all_three_parts(self):
        a = relation_key(relation())
        b = relation_key(relation(target="SQuAD"))
        assert a != b

    def test_keys_tolerate_missing_fields(self):
        assert entity_key({}) == ("", "")
        assert relation_key({}) == ("", "", "")
