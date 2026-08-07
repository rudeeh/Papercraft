"""
Integration tests for Phase 4 and 5 components combined with Phase 3 parser.

Tests validate:
- Parser -> Citation Extractor data flow
- Parser -> Entity Extractor data flow
- Full pipeline compatibility
"""

import pytest
from app.paper.parser import PaperParser
from app.citations.extractor import CitationExtractor
from app.citations.normalizer import CitationNormalizer
from app.graph.entity_extractor import EntityExtractor

SAMPLE_OCR_OUTPUT = [
    (1, """
    Attention Is All You Need

    Abstract
    We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.
    Experiments on WMT14 English-German translation task show state-of-the-art BLEU score.

    Introduction
    The Transformer is the first transduction model relying entirely on self-attention to compute representations of its input and output without using sequence-aligned RNNs or convolution.
    """),
    (2, """
    Methods
    The Transformer uses multi-head attention to allow the model to jointly attend to information from different representation subspaces at different positions.

    Experiments
    We evaluate our model on the WMT 2014 English-to-German translation task.
    We also evaluate on the ImageNet dataset for image classification.

    References
    [1] Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, and Illia Polosukhin. 2017. "Attention Is All You Need." In NIPS. doi: 10.1234/nips.2017
    [2] J. Deng, W. Dong, R. Socher, L.-J. Li, K. Li, and L. Fei-Fei. 2009. "ImageNet: A Large-Scale Hierarchical Image Database." In CVPR.
    """),
]

def test_full_extraction_pipeline_integration():
    """
    Test the integration of Parser, Citation Extractor, and Entity Extractor.
    """
    # 1. Parsing (Phase 3)
    parser = PaperParser()
    parsed_result = parser.parse(SAMPLE_OCR_OUTPUT)

    assert parsed_result.title is not None
    assert "Attention" in parsed_result.title
    assert len(parsed_result.sections) > 0

    # 2. Citation Extraction (Phase 4)
    # Using the full text for citation extraction
    full_text = "\n\n".join([text for _, text in SAMPLE_OCR_OUTPUT])
    citation_extractor = CitationExtractor()
    citation_normalizer = CitationNormalizer()

    raw_citations = citation_extractor.extract(full_text)
    normalized_refs = citation_normalizer.normalize_list(raw_citations["references"])

    assert len(normalized_refs) >= 2
    # Verify we found the DOI from the sample text
    assert any(ref.get("doi") == "10.1234/nips.2017" for ref in normalized_refs)

    # 3. Entity Extraction (Phase 5)
    # Entity extractor can take the parsed_result.to_dict()
    entity_extractor = EntityExtractor()
    entities = entity_extractor.extract(parsed_result.to_dict())

    assert len(entities) > 0

    entity_names = [e["name"].lower() for e in entities]
    entity_types = [e["type"] for e in entities]

    # Verify expected entities are found
    assert "transformer" in entity_names
    assert "imagenet" in entity_names

    assert "Method" in entity_types
    assert "Dataset" in entity_types

def test_entity_extractor_with_parser_output():
    """
    Specifically test that EntityExtractor correctly handles PaperParseResult format.
    """
    parser = PaperParser()
    parsed_result = parser.parse(SAMPLE_OCR_OUTPUT)

    extractor = EntityExtractor()
    # Test passing the result object directly
    entities = extractor.extract(parsed_result)

    assert len(entities) > 0
    assert any(e["name"] == "Transformer" for e in entities)
    assert any(e["type"] == "Method" for e in entities)

def test_citation_extractor_handles_parsed_references():
    """
    Test if CitationExtractor can be used on the reference section text identified by the parser.
    """
    parser = PaperParser()
    parsed_result = parser.parse(SAMPLE_OCR_OUTPUT)

    # Find the reference section text from parsed_result
    ref_section_text = ""
    for section in parsed_result.sections:
        if "reference" in section["heading"].lower() or "bibliography" in section["heading"].lower():
            ref_section_text = section["text"]
            break

    if ref_section_text:
        extractor = CitationExtractor()
        # The extractor usually expects full text to find the section,
        # but _parse_bibliography can take the section text directly
        refs = extractor._parse_bibliography(ref_section_text)
        assert len(refs) > 0
    else:
        # If parser didn't put it in sections, check if it found it in result.references
        assert len(parsed_result.references) > 0
