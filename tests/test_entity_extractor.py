import pytest

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


class TestRejectsNonEntities:
    """
    Regression cover for the extractor emitting function words and
    sentence fragments as Methods/Datasets/Metrics/Tasks (issue #11).

    The capture patterns anchor on capitalisation to spot proper nouns,
    but were being run case-insensitively, so "the model" yielded "the".
    """

    NAME_LIKE = {"Method", "Dataset", "Task", "Metric"}

    def _named(self, text):
        return {
            (e["type"], e["name"])
            for e in EntityExtractor().extract(text)
            if e["type"] in self.NAME_LIKE
        }

    @pytest.mark.parametrize(
        "sentence",
        [
            "The model was trained on a large corpus.",
            "All experiments use the same framework.",
            "This enables the model to reach convergence.",
            "Our approach denotes each stream of the video separately.",
            "We evaluate on the dataset described above.",
            "The majority of parameters are shared across the network.",
            "Video generation has evolved rapidly in recent years.",
        ],
    )
    def test_function_words_and_fragments_are_not_entities(self, sentence):
        assert self._named(sentence) == set()

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("The Transformer model outperforms LSTM.", ("Method", "Transformer")),
            ("We use a Multi-Head Attention architecture.", ("Method", "Multi-Head Attention")),
            ("Trained on the CIFAR-100 dataset.", ("Dataset", "CIFAR-100")),
            ("We evaluate BERT on the SQuAD dataset.", ("Dataset", "SQuAD")),
            ("Results are measured by BLEU score.", ("Metric", "BLEU")),
        ],
    )
    def test_real_entities_still_extracted(self, sentence, expected):
        assert expected in self._named(sentence)

    def test_does_not_join_two_entities_across_a_preposition(self):
        # The greedy dataset pattern used to capture "BERT on the SQuAD".
        assert ("Dataset", "BERT on the SQuAD") not in self._named(
            "We evaluate BERT on the SQuAD dataset."
        )

    def test_claims_may_still_be_statements(self):
        # Claim/Experiment names are statements by nature, so the
        # fragment rule must not apply to them.
        entities = EntityExtractor().extract(
            "We show that our method improves accuracy substantially."
        )
        assert any(e["type"] == "Claim" for e in entities)
