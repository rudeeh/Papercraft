import pytest
from app.graph.relation_extractor import RelationExtractor
from app.graph.ontology import OntologyValidator

SAMPLE_ENTITIES = [
    {"name": "Transformer", "type": "Method"},
    {"name": "RNN", "type": "Method"},
    {"name": "WMT14", "type": "Dataset"},
    {"name": "machine translation", "type": "Task"},
]

SAMPLE_TEXT = (
    "The Transformer outperforms traditional RNN models. "
    "Furthermore, the Transformer was evaluated on the WMT14 dataset. "
    "We designed the Transformer to solve the machine translation task."
)


def test_relation_extractor_basic_extraction():
    extractor = RelationExtractor()
    relations = extractor.extract(SAMPLE_ENTITIES, SAMPLE_TEXT)

    assert len(relations) > 0

    # Check relation keys and format (Task 6.2)
    for rel in relations:
        assert "source" in rel
        assert "source_type" in rel
        assert "relation" in rel
        assert "target" in rel
        assert "target_type" in rel
        assert "evidence" in rel

    # Check Method -> IMPROVES_UPON -> Method
    improves_rel = [
        r for r in relations
        if r["source"] == "Transformer" and r["target"] == "RNN" and r["relation"] == "IMPROVES_UPON"
    ]
    assert len(improves_rel) == 1
    assert "outperforms" in improves_rel[0]["evidence"]

    # Check Method -> USES_DATASET -> Dataset (mapped from "evaluated on")
    uses_dataset_rel = [
        r for r in relations
        if r["source"] == "Transformer" and r["target"] == "WMT14" and r["relation"] == "USES_DATASET"
    ]
    assert len(uses_dataset_rel) == 1
    assert "evaluated on" in uses_dataset_rel[0]["evidence"]

    # Check Method -> SOLVES_TASK -> Task (mapped from "designed... to solve")
    solves_task_rel = [
        r for r in relations
        if r["source"] == "Transformer" and r["target"] == "machine translation" and r["relation"] == "SOLVES_TASK"
    ]
    assert len(solves_task_rel) == 1
    assert "solve" in solves_task_rel[0]["evidence"]


def test_relations_conform_to_strict_ontology():
    extractor = RelationExtractor()
    relations = extractor.extract(SAMPLE_ENTITIES, SAMPLE_TEXT)

    validator = OntologyValidator()
    assert len(relations) > 0
    for rel in relations:
        # Assert each edge is strictly valid in our central ontology
        assert validator.validate_edge(
            rel["source_type"], rel["relation"], rel["target_type"]
        )


def test_empty_or_invalid_inputs_are_handled():
    extractor = RelationExtractor()

    # Empty entities
    assert extractor.extract([], SAMPLE_TEXT) == []
    # Empty text
    assert extractor.extract(SAMPLE_ENTITIES, "") == []
    # None parameters
    assert extractor.extract(None, SAMPLE_TEXT) == []
    assert extractor.extract(SAMPLE_ENTITIES, None) == []


def test_extracted_relations_deduplicated():
    extractor = RelationExtractor()
    duplicated_text = (
        "The Transformer outperforms traditional RNN models. "
        "Yes, the Transformer outperforms traditional RNN models indeed!"
    )
    relations = extractor.extract(SAMPLE_ENTITIES, duplicated_text)

    # We should only get 1 distinct relationship between Transformer and RNN
    transformer_rnn_rels = [
        r for r in relations
        if r["source"] == "Transformer" and r["target"] == "RNN" and r["relation"] == "IMPROVES_UPON"
    ]
    assert len(transformer_rnn_rels) == 1
