"""Tests for QueryClassifier (Phase 13.1)."""

import pytest

from app.retrieval.query_classifier import QueryClassifier, QueryType


@pytest.fixture
def classifier():
    return QueryClassifier()


class TestClassify:
    @pytest.mark.parametrize("query,expected", [
        ("Which papers cite the Transformer paper?", QueryType.CITATION),
        ("Who cites this work?", QueryType.CITATION),
        ("Show me the references for this paper.", QueryType.CITATION),
        ("How did attention mechanisms evolve from RNNs to Transformers?", QueryType.EVOLUTION),
        ("What is the history of neural machine translation?", QueryType.EVOLUTION),
        ("Compare BERT and GPT.", QueryType.COMPARISON),
        ("BERT vs GPT performance", QueryType.COMPARISON),
        ("What is the difference between CNNs and RNNs?", QueryType.COMPARISON),
        ("Which methods outperform ResNet?", QueryType.COMPARISON),
        ("Give me a survey of graph neural networks.", QueryType.SURVEY),
        ("Provide an overview of retrieval-augmented generation.", QueryType.SURVEY),
        ("What is the state of the art in question answering?", QueryType.SURVEY),
        ("What is BERT?", QueryType.ENTITY_LOOKUP),
        ("Who is the author of this paper?", QueryType.ENTITY_LOOKUP),
        ("Define self-attention.", QueryType.ENTITY_LOOKUP),
        ("Tell me about the Transformer architecture.", QueryType.ENTITY_LOOKUP),
        ("Explain how self-attention works.", QueryType.EXPLANATION),
        ("Why does dropout help regularization?", QueryType.EXPLANATION),
    ])
    def test_classifies_expected_type(self, classifier, query, expected):
        assert classifier.classify(query) == expected

    def test_empty_query_defaults_to_explanation(self, classifier):
        assert classifier.classify("") == QueryType.EXPLANATION
        assert classifier.classify("   ") == QueryType.EXPLANATION

    def test_case_insensitive(self, classifier):
        assert classifier.classify("WHO CITES THIS PAPER?") == QueryType.CITATION

    def test_citation_takes_precedence_over_comparison(self, classifier):
        # Contains both a citation cue and a comparison cue -- citation wins.
        query = "Compare the papers that cite the Transformer paper."
        assert classifier.classify(query) == QueryType.CITATION
