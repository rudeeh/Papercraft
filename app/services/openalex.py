"""
OpenAlex metadata enrichment.

The parser recovers whatever a PDF's first page happens to state -- often a
title and little else, and often mangled by OCR. OpenAlex fills in the rest
(canonical title, authors with affiliations, year, venue, DOI, citation
counts, referenced works) keyed off whichever identifier the parser did
manage to extract.

Resolution order is DOI -> arXiv ID -> title search, cheapest and most
certain first. Title search is last because it is the only one that can be
*wrong*: two papers can share a title, and OCR noise makes near-misses
common, so a title hit is only accepted above a similarity floor.

Every failure mode -- network error, 404, rate limit, garbage JSON --
returns None. Enrichment is strictly additive; a paper that OpenAlex has
never heard of must still ingest.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Below this title similarity a search hit is treated as a different paper.
# Tuned to tolerate OCR damage and punctuation drift while rejecting
# unrelated papers.
#
# Known limitation: character similarity cannot detect semantic negation.
# "Attention Is All You Need" vs "Attention Is Not All You Need" scores
# 0.93, and the gap only narrows as titles get longer, so no threshold that
# still tolerates OCR noise can separate them. That is why a title match is
# the last resort *and* why the result is labelled -- ``match_method`` on
# the enriched record says "title", and ``match_confidence`` carries the
# score, so a consumer can treat it as weaker evidence than a DOI hit
# instead of assuming all enrichment is equally certain.
TITLE_MATCH_THRESHOLD = 0.90

_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)$", re.I)
_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", re.I)


class OpenAlexClient:
    """Thin, failure-tolerant OpenAlex API client."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        mailto: Optional[str] = None,
        timeout: Optional[float] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url or settings.OPENALEX_API_URL).rstrip("/")
        self.mailto = mailto if mailto is not None else settings.OPENALEX_MAILTO
        self.timeout = timeout if timeout is not None else settings.OPENALEX_TIMEOUT_S
        self._client = client
        self._owns_client = client is None

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _params(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # OpenAlex asks callers to identify themselves; doing so moves the
        # request into the "polite pool", which has materially better
        # latency and rate limits than the anonymous one.
        params: Dict[str, Any] = dict(extra or {})
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        try:
            response = self.client.get(url, params=self._params(params))
        except httpx.HTTPError as exc:
            logger.warning("openalex_request_failed", url=url, error=str(exc))
            return None

        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.warning("openalex_bad_status", url=url, status=response.status_code)
            return None

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("openalex_bad_json", url=url, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        return self._get(f"/works/https://doi.org/{normalized}")

    def by_arxiv_id(self, arxiv_id: str) -> Optional[Dict[str, Any]]:
        normalized = normalize_arxiv_id(arxiv_id)
        if not normalized:
            return None
        # arXiv IDs aren't directly addressable as OpenAlex work IDs, so
        # this filters on the locator instead of fetching by path.
        payload = self._get(
            "/works", {"filter": f"locations.landing_page_url.search:arxiv.org/abs/{normalized}", "per_page": 1}
        )
        results = (payload or {}).get("results") or []
        return results[0] if results else None

    def by_title(self, title: str) -> Optional[Dict[str, Any]]:
        cleaned = " ".join((title or "").split())
        if len(cleaned) < 10:
            # Too short to identify anything; a two-word "title" from a
            # mis-parsed header would match essentially at random.
            return None

        payload = self._get("/works", {"search": cleaned, "per_page": 3})
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for candidate in (payload or {}).get("results") or []:
            candidate_title = candidate.get("title") or candidate.get("display_name") or ""
            score = title_similarity(cleaned, candidate_title)
            if score >= TITLE_MATCH_THRESHOLD and score > best_score:
                best, best_score = candidate, score

        if best is not None:
            # Stashed so resolve()/enrich() can report *how* certain the
            # match was, rather than presenting a fuzzy hit as a fact.
            best = dict(best)
            best["_papercraft_match_confidence"] = round(best_score, 4)
        return best

    def resolve(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Try each identifier in order of decreasing certainty.

        Returns ``(work, match_method)`` or None. The method matters: a DOI
        hit is an identity, a title hit is a guess.
        """
        for lookup, value, method in (
            (self.by_doi, doi, "doi"),
            (self.by_arxiv_id, arxiv_id, "arxiv"),
            (self.by_title, title, "title"),
        ):
            if not value:
                continue
            work = lookup(value)
            if work:
                return work, method
        return None

    def enrich(
        self,
        *,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve and flatten to Papercraft's metadata shape, or None."""
        resolved = self.resolve(doi=doi, arxiv_id=arxiv_id, title=title)
        if not resolved:
            return None

        work, method = resolved
        record = normalize_work(work)
        record["match_method"] = method
        # An exact identifier match is certain; a title match is only as
        # good as its similarity score.
        record["match_confidence"] = (
            work.get("_papercraft_match_confidence", TITLE_MATCH_THRESHOLD)
            if method == "title"
            else 1.0
        )
        return record


# ======================================================================
# Pure helpers (unit-testable without any network)
# ======================================================================

def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Strip any doi.org prefix and lowercase; None if it isn't a DOI."""
    if not doi:
        return None
    match = _DOI_PREFIX_RE.match(doi.strip())
    return match.group(1).lower() if match else None


def normalize_arxiv_id(arxiv_id: Optional[str]) -> Optional[str]:
    """Strip an 'arXiv:' prefix; None if it isn't an arXiv identifier."""
    if not arxiv_id:
        return None
    match = _ARXIV_RE.match(arxiv_id.strip())
    return match.group(1) if match else None


def title_similarity(a: str, b: str) -> float:
    """Case- and punctuation-insensitive similarity in [0, 1]."""
    def canon(s: str) -> str:
        return re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()

    left, right = canon(a), canon(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> Optional[str]:
    """
    OpenAlex ships abstracts as an inverted index ({word: [positions]}) for
    copyright reasons. Invert it back into running text.
    """
    if not inverted_index:
        return None
    positioned: List[tuple] = [
        (position, word)
        for word, positions in inverted_index.items()
        for position in positions
        if isinstance(position, int)
    ]
    if not positioned:
        return None
    positioned.sort()
    return " ".join(word for _, word in positioned)


def normalize_work(work: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an OpenAlex work into the fields the graph pipeline uses."""
    authorships = work.get("authorships") or []
    authors: List[Dict[str, Any]] = []
    institutions: List[str] = []

    for authorship in authorships:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if not name:
            continue
        affiliations = [
            inst.get("display_name")
            for inst in (authorship.get("institutions") or [])
            if inst.get("display_name")
        ]
        institutions.extend(affiliations)
        authors.append({
            "name": name,
            "orcid": author.get("orcid"),
            "affiliations": affiliations,
        })

    location = work.get("primary_location") or {}
    source = location.get("source") or {}

    return {
        "openalex_id": work.get("id"),
        "title": work.get("title") or work.get("display_name"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "doi": normalize_doi(work.get("doi")),
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "venue": source.get("display_name"),
        "type": work.get("type"),
        "is_open_access": (work.get("open_access") or {}).get("is_oa"),
        "pdf_url": location.get("pdf_url"),
        "cited_by_count": work.get("cited_by_count"),
        "referenced_works": work.get("referenced_works") or [],
        "concepts": [
            c.get("display_name") for c in (work.get("concepts") or []) if c.get("display_name")
        ],
        "authors": authors,
        # Order-preserving dedupe: an institution repeated across authors
        # should appear once, but the first-listed one is usually the
        # corresponding author's and worth keeping first.
        "institutions": list(dict.fromkeys(institutions)),
        "metadata_source": "openalex",
    }
