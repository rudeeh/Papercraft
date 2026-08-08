"""
Paper Parser Module

Extracts structured content from PDFs including:
- Title, abstract, sections
- References with metadata
- Page information

Output format:
{
  "title": str,
  "abstract": str,
  "sections": [{"heading": str, "text": str}],
  "references": [{"title": str, "authors": list, "year": int, "doi": str, "arxiv_id": str}],
  "raw_pages": [(page_num, text)]  # For compatibility with existing chunking
}
"""

import structlog
from typing import Dict, List, Tuple, Optional
import re

logger = structlog.get_logger()


class PaperParseResult:
    """Structured output from paper parsing."""

    def __init__(self):
        self.title: Optional[str] = None
        self.abstract: Optional[str] = None
        self.sections: List[Dict[str, str]] = []  # [{"heading": str, "text": str}]
        self.references: List[Dict] = []  # [{title, authors, year, doi, arxiv_id}]
        self.raw_pages: List[Tuple[int, str]] = []  # [(page_num, text)]
        # This paper's own identity (not a cited paper's) -- printed in the
        # header of the first page for arXiv preprints / journal articles.
        # Used to resolve citation stubs when an earlier-ingested paper
        # already cites this one (see GraphRepository.resolve_citation_stub).
        self.arxiv_id: Optional[str] = None
        self.doi: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "title": self.title,
            "abstract": self.abstract,
            "sections": self.sections,
            "references": self.references,
            "raw_pages": self.raw_pages,
            "arxiv_id": self.arxiv_id,
            "doi": self.doi,
        }


class PaperParser:
    """
    Parse research paper PDFs into structured content.

    This parser works with output from OCR extraction and identifies:
    1. Title (usually first meaningful line)
    2. Abstract section
    3. Named sections (Introduction, Methods, Results, etc.)
    4. Reference section
    """

    # Common section headings in research papers
    SECTION_PATTERNS = [
        r"^abstract",
        r"^introduction",
        r"^related work",
        r"^background",
        r"^method(ology)?",
        r"^approach",
        r"^system",
        r"^experiment(s)?",
        r"^result(s)?",
        r"^evaluation",
        r"^discussion",
        r"^conclusion(s)?",
        r"^future work",
        r"^references?",
        r"^bibliography",
        r"^appendix",
        r"^supplementary",
    ]

    def __init__(self):
        self.section_patterns = [re.compile(p, re.IGNORECASE) for p in self.SECTION_PATTERNS]

    def parse(self, pages_text: List[Tuple[int, str]]) -> PaperParseResult:
        """
        Parse paper from OCR-extracted pages.

        Args:
            pages_text: List of (page_num, text) tuples from OCR extraction

        Returns:
            PaperParseResult with structured content
        """
        result = PaperParseResult()
        result.raw_pages = pages_text

        # Combine all pages into one text for initial processing
        full_text = "\n\n".join([text for _, text in pages_text])

        # Extract title (usually first non-empty line or first "heading-like" line)
        result.title = self._extract_title(full_text)

        # Extract abstract
        result.abstract = self._extract_abstract(full_text)

        # Extract sections
        result.sections = self._extract_sections(full_text)

        # Extract references
        result.references = self._extract_references(full_text)

        # Extract this paper's own arXiv ID / DOI from the first page only
        # (where preprints/journals print it) -- searching the full text
        # would pick up a *cited* paper's identifier from the references
        # section instead of this paper's own.
        first_page_text = pages_text[0][1] if pages_text else ""
        result.arxiv_id = self._extract_self_arxiv_id(first_page_text)
        result.doi = self._extract_self_doi(first_page_text)

        logger.info(
            "paper_parsed",
            title=result.title,
            abstract_length=len(result.abstract) if result.abstract else 0,
            num_sections=len(result.sections),
            num_references=len(result.references),
            arxiv_id=result.arxiv_id,
            doi=result.doi,
        )

        return result

    # arXiv IDs / DOIs are conventionally printed in the header, right by
    # the title -- restricting to this window (rather than the whole first
    # page) keeps a short/single-page paper's own References section from
    # being mistaken for its self-identity, which would otherwise corrupt
    # citation-stub resolution (see docs/decisions.md).
    _SELF_IDENTITY_WINDOW = 600

    def _extract_self_arxiv_id(self, first_page_text: str) -> Optional[str]:
        """Extract this paper's own arXiv ID (e.g. printed as 'arXiv:2101.00001')."""
        header = first_page_text[: self._SELF_IDENTITY_WINDOW]
        match = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", header, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_self_doi(self, first_page_text: str) -> Optional[str]:
        """Extract this paper's own DOI (e.g. printed as 'doi: 10.1234/abcd')."""
        header = first_page_text[: self._SELF_IDENTITY_WINDOW]
        match = re.search(r"\bdoi:?\s*(10\.\d{4,9}/\S+)", header, re.IGNORECASE)
        return match.group(1).rstrip(".,;)") if match else None

    def _extract_title(self, text: str) -> Optional[str]:
        """
        Extract paper title.

        Simple heuristic: first 1-3 non-empty lines that are reasonably short
        and formatted like a title.
        """
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if line and 10 < len(line) < 300:  # Title is usually 10-300 chars
                # Heuristic: avoid lines that look like section headers
                if not any(
                    pattern.match(line) for pattern in self.section_patterns
                ):
                    return line
        return None

    def _extract_abstract(self, text: str) -> Optional[str]:
        """
        Extract abstract section.

        Looks for "Abstract" section and extracts until next major section.
        """
        lines = text.split("\n")
        abstract_start = None

        for index, line in enumerate(lines):
            stripped = line.strip()
            if self._is_section_heading(stripped, allowed_prefixes=("abstract",)):
                abstract_start = index + 1
                break

        if abstract_start is None:
            return None

        abstract_lines = []
        for line in lines[abstract_start:]:
            stripped = line.strip()
            if stripped and self._is_section_heading(stripped):
                break
            abstract_lines.append(line)

        abstract_text = "\n".join(abstract_lines).strip()
        if not abstract_text:
            return None

        return re.sub(r"\s+", " ", abstract_text)

    def _extract_sections(self, text: str) -> List[Dict[str, str]]:
        """
        Extract named sections (Introduction, Methods, Results, etc.).

        Returns list of {heading, text} dicts.
        """
        sections = []

        # Find all section headings
        section_starts = []
        for i, line in enumerate(text.split("\n")):
            stripped = line.strip()
            if self._is_section_heading(stripped):
                section_starts.append((i, stripped))

        # Extract text for each section
        lines = text.split("\n")
        for idx, (line_num, heading) in enumerate(section_starts):
            # Find end of this section (start of next section or end of document)
            if idx + 1 < len(section_starts):
                end_line_num = section_starts[idx + 1][0]
            else:
                end_line_num = len(lines)

            section_text = "\n".join(lines[line_num + 1 : end_line_num]).strip()
            section_text = re.sub(r"\s+", " ", section_text)  # Normalize whitespace

            sections.append({"heading": heading, "text": section_text})

        return sections

    def _extract_references(self, text: str) -> List[Dict]:
        """
        Extract reference section.

        Simple extraction: find "References" section and parse lines.
        More sophisticated parsing (BibTeX, structured formats) can be added later.
        """
        references = []
        lines = text.split("\n")
        ref_start = None

        for index, line in enumerate(lines):
            stripped = line.strip()
            if self._is_section_heading(stripped, allowed_prefixes=("references", "bibliography")):
                ref_start = index + 1
                break

        if ref_start is None:
            return references

        ref_lines = lines[ref_start:]

        for line in ref_lines:
            line = line.strip()
            if not line or len(line) < 10:
                continue

            # Try to parse reference (very basic)
            ref_dict = self._parse_reference_line(line)
            if ref_dict:
                references.append(ref_dict)

        return references

    def _is_section_heading(
        self, line: str, allowed_prefixes: Tuple[str, ...] = None
    ) -> bool:
        """Return True when a stripped line looks like a known section heading."""
        if not line:
            return False

        if allowed_prefixes is not None:
            normalized = line.lower().rstrip(":")
            return any(normalized.startswith(prefix) for prefix in allowed_prefixes)

        return any(pattern.match(line) for pattern in self.section_patterns)

    def _parse_reference_line(self, line: str) -> Optional[Dict]:
        """
        Parse a single reference line.

        Extracts: title, authors, year, DOI, arXiv ID (if present).
        This is a simplified parser; production systems would use better tools.
        """
        if len(line) < 10:
            return None

        ref = {
            "title": None,
            "authors": [],
            "year": None,
            "doi": None,
            "arxiv_id": None,
        }

        # Extract DOI
        doi_match = re.search(r"doi:\s*([\d.]+/[\w./-]+)", line, re.IGNORECASE)
        if doi_match:
            ref["doi"] = doi_match.group(1)

        # Extract arXiv ID
        arxiv_match = re.search(r"arxiv:\s*(\d+\.\d+)", line, re.IGNORECASE)
        if arxiv_match:
            ref["arxiv_id"] = arxiv_match.group(1)

        # Extract year (4-digit number likely to be a year)
        year_match = re.search(r"\b(19|20)\d{2}\b", line)
        if year_match:
            ref["year"] = int(year_match.group(0))

        # Extract title: typically between quotes or after authors
        # Simple heuristic: take the first meaningful quoted or long substring
        title_match = re.search(r'"([^"]+)"', line)
        if title_match:
            ref["title"] = title_match.group(1)
        else:
            # Fallback: use first part of the line as title
            ref["title"] = line[:100]

        # Extract authors (heuristic: names before year or DOI)
        authors_section = line.split(str(ref["year"]) if ref["year"] else line[:50])[0]
        # Very simplified: split by "and" or ","
        potential_authors = re.split(r"\s+and\s+|,", authors_section)
        ref["authors"] = [a.strip() for a in potential_authors if a.strip()]

        return ref
