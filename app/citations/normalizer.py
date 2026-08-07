"""
Citation Normalizer Module (Phase 4)

Normalizes raw reference strings into structured citation records
suitable for graph construction.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
import structlog

logger = structlog.get_logger()


class CitationNormalizer:
    """
    Normalize raw citation strings into structured records.

    Produces dicts with consistent keys: title, authors, year, doi, arxiv_id, ref_id.
    """

    def normalize_list(self, references: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Normalize a list of raw reference dicts.

        Each input may have 'raw_text' and optional extracted fields.
        Returns a list with consistent, cleaned fields. References that refer
        to the same work (matching DOI or arXiv ID) are merged into a single
        record, with all their source ref_ids collected into ``ref_ids``.
        """
        normalized = []

        for ref in references:
            # If the reference already has structured fields, use them
            if ref.get("title") or ref.get("doi") or ref.get("arxiv_id"):
                norm = self._normalize_structured(ref)
            elif ref.get("raw_text"):
                norm = self._normalize_raw(ref["raw_text"])
            else:
                continue

            # Ensure ref_id exists
            if not norm.get("ref_id"):
                norm["ref_id"] = ref.get("ref_id", f"ref_{len(normalized)}")
            norm["ref_ids"] = [norm["ref_id"]] if norm.get("ref_id") else []

            normalized.append(norm)

        merged = self._merge_duplicates(normalized)
        logger.info("citations_normalized", count=len(merged))
        return merged

    def _merge_duplicates(
        self, normalized: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Merge records that refer to the same work (shared DOI or arXiv ID).

        The first-seen record's scalar fields (title, authors, year) are kept
        as canonical; every later duplicate's ref_id is folded into the
        survivor's ``ref_ids`` list instead of producing a separate record.
        """
        seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
        result: List[Dict[str, Any]] = []

        for norm in normalized:
            key: Optional[Tuple[str, str]] = None
            if norm.get("doi"):
                key = ("doi", norm["doi"])
            elif norm.get("arxiv_id"):
                key = ("arxiv", norm["arxiv_id"])

            if key and key in seen:
                seen[key]["ref_ids"].extend(norm["ref_ids"])
                continue

            result.append(norm)
            if key:
                seen[key] = norm

        return result

    def _normalize_structured(self, ref: Dict[str, Any]) -> Dict[str, Any]:
        """Clean up an already-partially-structured reference."""
        norm: Dict[str, Any] = {}

        norm["title"] = self._clean_string(ref.get("title"))
        norm["authors"] = [self._clean_string(a) for a in ref.get("authors", []) if self._clean_string(a)]
        norm["year"] = ref.get("year")
        norm["doi"] = self._clean_doi(ref.get("doi"))
        norm["arxiv_id"] = self._clean_arxiv(ref.get("arxiv_id"))
        norm["ref_id"] = ref.get("ref_id", "")

        return norm

    def _normalize_raw(self, raw_text: str) -> Dict[str, Any]:
        """
        Extract what we can from an unparsed reference string.
        """
        norm: Dict[str, Any] = {
            "title": "",
            "authors": [],
            "year": None,
            "doi": None,
            "arxiv_id": None,
            "ref_id": "",
            "raw_text": raw_text,
        }

        # DOI
        doi_match = re.search(
            r"(?:doi|DOI)[\s:]*10\.(\d{4,})/(\S+)", raw_text, re.IGNORECASE
        )
        if doi_match:
            norm["doi"] = f"10.{doi_match.group(1)}/{doi_match.group(2)}"

        # arXiv ID
        arxiv_match = re.search(
            r"arXiv[\s.:]*(\d{4}\.\d{4,5})", raw_text, re.IGNORECASE
        )
        if arxiv_match:
            norm["arxiv_id"] = arxiv_match.group(1)

        # Year
        year_match = re.search(r"\b(19|20)\d{2}\b", raw_text)
        if year_match:
            norm["year"] = int(year_match.group(0))

        # Title (between quotes, or after year)
        title = self._extract_title(raw_text)
        if title:
            norm["title"] = title

        # Authors (text before year)
        authors = self._extract_authors(raw_text)
        if authors:
            norm["authors"] = authors

        return norm

    def _extract_title(self, text: str) -> str:
        # Quoted title
        m = re.search(r'"([^"]+)"', text)
        if m:
            return m.group(1).strip()
        m = re.search(r"'([^']+)'", text)
        if m:
            return m.group(1).strip()

        # After year
        year_m = re.search(r"(?:19|20)\d{2}[.,)\]]\s*(.+?)(?:\.\s|$)", text)
        if year_m:
            t = year_m.group(1).strip()
            t = re.sub(r"\s*(?:doi|arXiv|available|retrieved|http).*$", "", t, flags=re.IGNORECASE)
            if 5 < len(t) < 300:
                return t
        return ""

    def _extract_authors(self, text: str) -> List[str]:
        year_m = re.search(r"(?:19|20)\d{2}", text)
        if not year_m:
            return []
        before = text[:year_m.start()]
        parts = re.split(r"\s*[,;]\s*", before)
        authors = []
        for p in parts:
            p = re.sub(r"^\[\d+\]\s*", "", p.strip())
            p = re.sub(r"\s*(?:and|&)\s*$", "", p)
            if p and 2 < len(p) < 80 and not re.match(r"^\d+$", p):
                authors.append(p)
        return authors

    @staticmethod
    def _clean_string(value: Optional[str]) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    @staticmethod
    def _clean_doi(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = re.sub(r"\s+", "", str(value))
        if not value.lower().startswith("10."):
            return None
        # DOIs are case-insensitive; normalize to lowercase so equivalent
        # DOIs from different sources dedupe correctly.
        return value.lower()

    @staticmethod
    def _clean_arxiv(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        value = re.sub(r"\s+", "", str(value))
        m = re.match(r"^(\d{4}\.\d{4,5})$", value)
        return m.group(1) if m else None