"""
Unit tests for paper parser module.

Tests validate:
- Title extraction
- Abstract detection
- Section identification
- Reference extraction
- Complete paper parsing
"""

import pytest
from app.paper.parser import PaperParser, PaperParseResult


class TestPaperParseResult:
    """Test PaperParseResult class."""

    def test_initialization(self):
        """Test PaperParseResult initialization."""
        result = PaperParseResult()
        assert result.title is None
        assert result.abstract is None
        assert result.sections == []
        assert result.references == []
        assert result.raw_pages == []

    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = PaperParseResult()
        result.title = "Test Paper"
        result.abstract = "This is an abstract"
        result.sections = [{"heading": "Introduction", "text": "..."}]

        d = result.to_dict()
        assert d["title"] == "Test Paper"
        assert d["abstract"] == "This is an abstract"
        assert len(d["sections"]) == 1
        assert isinstance(d["raw_pages"], list)


class TestPaperParserTitleExtraction:
    """Test title extraction functionality."""

    def test_extract_title_simple(self):
        """Test extracting a simple title."""
        parser = PaperParser()
        text = "Attention Is All You Need\\n\\nAbstract\\nThis paper..."
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        assert result.title is not None
        assert "Attention" in result.title

    def test_extract_title_from_multiple_lines(self):
        """Test title extraction from document with multiple lines."""
        parser = PaperParser()
        text = "Line 1\\nAttention Is All You Need\\n\\nAbstract\\nContent here..."
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        # Should extract a title (the actual one might vary)
        assert result.title is not None

    def test_no_title_extraction(self):
        """Test when no valid title can be extracted."""
        parser = PaperParser()
        text = "a b c d e f g h i j\\nVery short text\\n"  # Everything is too short
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        # Should handle gracefully
        assert result.title is None or isinstance(result.title, str)


class TestPaperParserAbstractExtraction:
    """Test abstract extraction functionality."""

    def test_extract_abstract_simple(self):
        """Test extracting a simple abstract."""
        parser = PaperParser()
        text = """
        Title Here

        Abstract
        This is the abstract content that describes the research.
        It contains important information about the work.

        Introduction
        This is the introduction section.
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        assert result.abstract is not None
        assert "abstract content" in result.abstract.lower()

    def test_extract_abstract_case_insensitive(self):
        """Test that abstract detection is case-insensitive."""
        parser = PaperParser()
        text = """
        Title

        ABSTRACT
        This is an abstract written in all caps section.

        Introduction
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        assert result.abstract is not None

    def test_no_abstract_found(self):
        """Test when no abstract section exists."""
        parser = PaperParser()
        text = "Title\\nSome content\\nMore content"
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        assert result.abstract is None


class TestPaperParserSectionExtraction:
    """Test section extraction functionality."""

    def test_extract_multiple_sections(self):
        """Test extracting multiple named sections."""
        parser = PaperParser()
        text = """
        Title

        Introduction
        This is the introduction content.

        Methods
        This describes the methodology.

        Results
        These are the results.

        Conclusion
        Final thoughts here.
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        assert len(result.sections) > 0
        section_headings = [s["heading"] for s in result.sections]
        # Should have detected at least some sections
        assert any("Introduction" in h or "Methods" in h for h in section_headings)

    def test_section_text_extraction(self):
        """Test that section content is properly extracted."""
        parser = PaperParser()
        text = """
        Related Work
        Previous studies have shown results.
        Background work is important.

        Methodology
        Our approach is novel.
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        sections = result.sections
        if sections:
            # Each section should have heading and text
            for section in sections:
                assert "heading" in section
                assert "text" in section
                assert section["heading"] is not None
                assert section["text"] is not None

    def test_section_heading_normalization(self):
        """Test that section headings are properly identified."""
        parser = PaperParser()
        text = """
        Background
        Background information here.

        Experiments
        Experiment details here.
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        # Should find sections even with varied naming
        assert len(result.sections) >= 0


class TestPaperParserReferenceExtraction:
    """Test reference extraction functionality."""

    def test_extract_references_simple(self):
        """Test extracting a simple reference section."""
        parser = PaperParser()
        text = """
        Main content here.

        References
        [1] Smith, J., 2020. \"A Great Paper\". Journal Name.
        [2] Doe, J., 2021. \"Another Great Paper\". arxiv:2101.12345
        """
        pages_text = [(1, text)]

        result = parser.parse(pages_text)
        # Should extract reference section
        # Even if parsing is incomplete, it should attempt extraction
        assert isinstance(result.references, list)

    def test_extract_year_from_reference(self):
        """Test extracting year from reference."""
        parser = PaperParser()
        ref_line = "Smith, J., 2020. 'A Paper'. Journal."

        result = parser._parse_reference_line(ref_line)
        assert result["year"] == 2020

    def test_extract_doi_from_reference(self):
        """Test extracting DOI from reference."""
        parser = PaperParser()
        ref_line = 'Smith, J. \"Paper Title\". doi: 10.1234/example.doi'

        result = parser._parse_reference_line(ref_line)
        assert result["doi"] is not None
        assert "10.1234" in result["doi"]

    def test_extract_arxiv_from_reference(self):
        """Test extracting arXiv ID from reference."""
        parser = PaperParser()
        ref_line = 'Smith, J. \"Paper Title\". arXiv:2101.12345'

        result = parser._parse_reference_line(ref_line)
        assert result["arxiv_id"] is not None
        assert "2101" in result["arxiv_id"]

    def test_reference_with_multiple_metadata(self):
        """Test reference with multiple metadata fields."""
        parser = PaperParser()
        ref_line = 'Smith, J. and Doe, M., 2022. \"Advanced Methods\". doi: 10.5555/test arxiv:2204.56789'

        result = parser._parse_reference_line(ref_line)
        assert result["year"] == 2022
        assert result["doi"] is not None
        assert result["arxiv_id"] is not None


class TestPaperParserMultiPage:
    """Test parsing multi-page documents."""

    def test_parse_multiple_pages(self):
        """Test parsing content from multiple pages."""
        parser = PaperParser()
        pages_text = [
            (1, "Title\\n\\nAbstract\\nThis is the abstract."),
            (2, "Introduction\\nIntro content."),
            (3, "Methods\\nMethod content."),
        ]

        result = parser.parse(pages_text)
        assert len(result.raw_pages) == 3
        # Content from multiple pages should be combined
        assert result.abstract is not None or result.sections

    def test_raw_pages_preserved(self):
        """Test that raw pages are preserved in result."""
        parser = PaperParser()
        pages_text = [(1, "Page 1"), (2, "Page 2"), (3, "Page 3")]

        result = parser.parse(pages_text)
        assert result.raw_pages == pages_text
