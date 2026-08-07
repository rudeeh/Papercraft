from app.graph.entity_extractor import EntityExtractor
from app.graph.ontology import OntologyValidator


SAMPLE_SECTIONS = [
    {
        "heading": "Abstract",
        "text": (
            "We introduce the Transformer model for machine translation. "
            "Results show state-of-the-art BLEU on WMT14."
        ),
    },
    {
        "heading": "Experiments",
        "text": (
            "We evaluate on the ImageNet dataset and report accuracy and F1. "
            "The ablation experiment compares against baseline CNN models."
        ),
    },
]


def test_extracts_phase_5_entity_types_from_sections():
    extractor = EntityExtractor()
    entities = extractor.extract(SAMPLE_SECTIONS)

    entity_pairs = {(entity["type"], entity["name"]) for entity in entities}

    assert ("Method", "Transformer") in entity_pairs
    assert ("Dataset", "WMT14") in entity_pairs
    assert ("Dataset", "ImageNet") in entity_pairs
    assert ("Task", "machine translation") in entity_pairs
    assert ("Metric", "BLEU") in entity_pairs
    assert ("Metric", "accuracy") in entity_pairs
    assert any(entity["type"] == "Claim" for entity in entities)
    assert any(entity["type"] == "Experiment" for entity in entities)


def test_extracted_entities_use_valid_ontology_types_only():
    extractor = EntityExtractor()
    entities = extractor.extract(SAMPLE_SECTIONS)

    assert entities
    for entity in entities:
        assert entity["type"] in EntityExtractor.ENTITY_TYPES
        assert OntologyValidator.validate_node_type(entity["type"])


def test_every_entity_has_source_section_and_evidence():
    extractor = EntityExtractor()
    entities = extractor.extract(SAMPLE_SECTIONS)

    assert entities
    for entity in entities:
        assert entity["name"].strip()
        assert entity["source_section"].strip()
        assert entity["evidence"].strip()


def test_empty_and_malformed_input_is_ignored():
    extractor = EntityExtractor()

    assert extractor.extract(None) == []
    assert extractor.extract("") == []
    assert extractor.extract([{}, {"heading": "Methods", "text": ""}, object()]) == []


def test_accepts_parser_result_shape():
    extractor = EntityExtractor()
    parsed_paper = {
        "abstract": "We propose GraphRAG for document retrieval.",
        "sections": [
            {
                "heading": "Evaluation",
                "text": "Experiments on SQuAD report F1 and accuracy.",
            }
        ],
    }

    entities = extractor.extract(parsed_paper)
    entity_pairs = {(entity["type"], entity["name"]) for entity in entities}

    assert ("Method", "GraphRAG") in entity_pairs
    assert ("Task", "document retrieval") in entity_pairs
    assert ("Dataset", "SQuAD") in entity_pairs
    assert ("Metric", "F1") in entity_pairs
