import pytest
from app.citations.extractor import CitationExtractor
from app.citations.normalizer import CitationNormalizer

SAMPLE_TEXT = """
This is a research paper about GraphRAG.
As mentioned in [1], knowledge graphs are useful.
Other studies (Author et al., 2022) have shown similar results.
Multiple citations [2, 3] or ranges [1-3] are also common.

References
[1] Smith, J., and Doe, A. "Graph Retrieval Augmented Generation." Journal of AI, 2021. doi: 10.1234/graphrag.2021
[2] Author, B. (2022). "Semantic Search in PDFs." arXiv: 2201.12345
[3] Brown, C. "Large Language Models for Research." 2023.
"""

def test_citation_extraction():
    extractor = CitationExtractor()
    result = extractor.extract(SAMPLE_TEXT)

    assert len(result["references"]) == 3
    assert result["references"][0]["ref_id"] == "1"
    assert result["references"][0]["year"] == 2021
    assert result["references"][0]["doi"] == "10.1234/graphrag.2021"

    assert result["references"][1]["ref_id"] == "2"
    assert result["references"][1]["arxiv_id"] == "2201.12345"

    # Test mentions
    assert len(result["mentions"]) > 0
    # Check if [1] is found
    mention_ids = [m["ref_id"] for m in result["mentions"]]
    assert "1" in mention_ids
    assert "2" in mention_ids
    assert "3" in mention_ids

def test_citation_normalization():
    normalizer = CitationNormalizer()
    refs = [
        {
            "title": "Graph RAG  ",
            "authors": ["Smith, J. ", " Doe, A."],
            "year": 2021,
            "doi": "10.1234/GRAPHRAG.2021",
            "ref_id": "1"
        },
        {
            "title": "Different Title for same DOI",
            "doi": "10.1234/graphrag.2021",
            "ref_id": "99"
        }
    ]

    normalized = normalizer.normalize_list(refs)

    assert len(normalized) == 1
    assert normalized[0]["title"] == "Graph RAG"
    assert "Smith, J." in normalized[0]["authors"] or "Smith, J" in normalized[0]["authors"]
    assert normalized[0]["doi"] == "10.1234/graphrag.2021"
    assert "1" in normalized[0]["ref_ids"]
    assert "99" in normalized[0]["ref_ids"]

def test_citation_range_extraction():
    extractor = CitationExtractor()
    text = "Detailed in [1-3]."
    refs = [{"ref_id": "1"}, {"ref_id": "2"}, {"ref_id": "3"}]
    mentions = extractor._find_mentions(text, refs)

    assert len(mentions) == 3
    ref_ids = sorted([m["ref_id"] for m in mentions])
    assert ref_ids == ["1", "2", "3"]
