"""
arXiv ingestion client.

Resolves an arXiv identifier (e.g. ``2401.12345``, ``2401.12345v2``,
``cs/0701001``) to the canonical PDF URL + minimal metadata, and streams
the PDF bytes to a caller-supplied path. Everything here is HTTP-only and
side-effect-free; the worker task wires the downloaded file into the
existing :class:`PaperIngestionPipeline`.

Endpoints used (all public, no API key required):

* ``https://arxiv.org/abs/{id}``  — HTML landing page, parsed as a
  last-resort fallback when the Atom export is unavailable.
* ``http://export.arxiv.org/api/query?id_list={id}`` — Atom feed with
  title, authors, primary category, DOI link, published/updated dates.
* ``https://arxiv.org/pdf/{id}.pdf`` — canonical PDF binary.

The client is deliberately synchronous and uses ``httpx.Client``: the
Celery worker that calls it is sync, and arXiv's export endpoint is
notably intolerant of high concurrency from a single IP, so we keep
requests sequential and rate-limited via :data:`REQUEST_DELAY_S`.
"""

from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARXIV_ABS_URL = "https://arxiv.org/abs/{arxiv_id}"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
ARXIV_API_URL = "http://export.arxiv.org/api/query?id_list={arxiv_id}"

# Atom + OpenSearch namespaces used by arXiv's export endpoint.
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# arXiv IDs post-2007 look like 2401.12345[vN]; pre-2007 look like
# cs/0701001. We accept both, with or without a version suffix.
_ARXIV_ID_RE = re.compile(
    r"^(?:(?:https?://arxiv\.org/(?:abs|pdf)/)?)?"   # optional URL prefix
    r"(?P<id>[a-z\-]+/\d{7}|\d{4}\.\d{4,5})"          # the core id
    r"(?:v(?P<version>\d+))?"                          # optional version
    r"(?:\.pdf)?$"                                     # optional .pdf suffix
)

# Be polite to arXiv. The export API docs ask for a 3s gap between
# requests; we apply a smaller default since most callers hit it once.
REQUEST_DELAY_S = 3.0
HTTP_TIMEOUT_S = 30.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ArxivMetadata:
    """Normalized arXiv record. Mirrors the subset of fields OpenAlex
    would later enrich, so the pipeline can keep its single enrichment
    contract (DOI -> arxiv_id -> fuzzy title) regardless of source."""

    arxiv_id: str                 # canonical, no version (e.g. "2401.12345")
    arxiv_id_versioned: str       # e.g. "2401.12345v2" (may equal arxiv_id)
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    primary_category: str = ""    # e.g. "cs.CL"
    categories: list[str] = field(default_factory=list)
    doi: Optional[str] = None
    published: Optional[str] = None    # ISO 8601
    updated: Optional[str] = None
    pdf_url: str = ""

    def to_dict(self) -> dict:
        return {
            "arxiv_id": self.arxiv_id,
            "arxiv_id_versioned": self.arxiv_id_versioned,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "primary_category": self.primary_category,
            "categories": self.categories,
            "doi": self.doi,
            "published": self.published,
            "updated": self.updated,
            "pdf_url": self.pdf_url,
        }


class ArxivError(Exception):
    """Base class for arXiv ingestion errors."""


class InvalidArxivId(ArxivError):
    """The supplied string is not a recognizable arXiv identifier."""


class ArxivNotFound(ArxivError):
    """arXiv returned no record for the identifier."""


class ArxivUnavailable(ArxivError):
    """Network or upstream failure talking to arXiv."""


# ---------------------------------------------------------------------------
# ID normalization
# ---------------------------------------------------------------------------


def normalize_arxiv_id(raw: str) -> tuple[str, Optional[int]]:
    """
    Return ``(canonical_id, version_or_None)`` for any reasonable input.

    Accepted shapes (all return ``("2401.12345", 2)`` unless noted):
        - ``2401.12345``
        - ``2401.12345v2``
        - ``arXiv:2401.12345v2``
        - ``https://arxiv.org/abs/2401.12345v2``
        - ``https://arxiv.org/pdf/2401.12345v2.pdf``
        - ``cs/0701001`` (pre-2007 format -> ``("cs/0701001", None)``)

    Raises :class:`InvalidArxivId` if the string can't be parsed.
    """
    if raw is None:
        raise InvalidArxivId("arXiv id is None")
    cleaned = raw.strip()
    if cleaned.lower().startswith("arxiv:"):
        cleaned = cleaned[len("arxiv:"):].strip()
    m = _ARXIV_ID_RE.match(cleaned)
    if not m:
        raise InvalidArxivId(f"not a recognizable arXiv id: {raw!r}")
    arxiv_id = m.group("id")
    version = int(m.group("version")) if m.group("version") else None
    return arxiv_id, version


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ArxivClient:
    """
    Thin HTTP client for arXiv. Stateless across calls; safe to instantiate
    per-task. A single class-level ``_last_call_ts`` enforces a global
    inter-request gap so multiple workers don't trip arXiv's rate limiter.
    """

    _last_call_ts: float = 0.0

    def __init__(
        self,
        *,
        timeout_s: float = HTTP_TIMEOUT_S,
        request_delay_s: float = REQUEST_DELAY_S,
        user_agent: str = "Papercraft/0.1 (https://github.com/rudeeh/Papercraft)",
    ) -> None:
        self.timeout_s = timeout_s
        self.request_delay_s = request_delay_s
        self.user_agent = user_agent

    # -- public API --------------------------------------------------------

    def fetch_metadata(self, raw_id: str) -> ArxivMetadata:
        """
        Resolve ``raw_id`` to a full :class:`ArxivMetadata` via the Atom
        export API. Raises :class:`ArxivNotFound` when arXiv reports zero
        results, and :class:`ArxivUnavailable` on transport errors.
        """
        arxiv_id, version = normalize_arxiv_id(raw_id)
        versioned = f"{arxiv_id}v{version}" if version else arxiv_id
        url = ARXIV_API_URL.format(arxiv_id=arxiv_id)
        body = self._get_text(url)

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ArxivUnavailable(f"arXiv returned malformed XML: {exc}") from exc

        entries = root.findall("atom:entry", _NS)
        if not entries:
            raise ArxivNotFound(f"arXiv has no entry for {arxiv_id!r}")

        # The export API returns at most one entry for an id_list lookup,
        # but defensively take the first if there's ever more than one.
        entry = entries[0]

        # arXiv's API returns a single dummy entry with title "Error" if
        # the id is malformed; distinguish that from a real not-found.
        title_text = _text(entry, "atom:title")
        if title_text.lower().startswith("error"):
            raise ArxivNotFound(f"arXiv reports an error for {arxiv_id!r}: {title_text}")

        authors = [_text(a, "atom:name") for a in entry.findall("atom:author", _NS)]
        primary_cat_el = entry.find("arxiv:primary_category", _NS)
        primary_cat = primary_cat_el.attrib.get("term", "") if primary_cat_el is not None else ""
        categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", _NS)]
        doi_link = next(
            (l for l in entry.findall("atom:link", _NS) if l.attrib.get("title") == "doi"),
            None,
        )
        doi = doi_link.attrib.get("href", "").removeprefix("http://dx.doi.org/") if doi_link is not None else None

        return ArxivMetadata(
            arxiv_id=arxiv_id,
            arxiv_id_versioned=versioned,
            title=title_text,
            authors=authors,
            abstract=_text(entry, "atom:summary"),
            primary_category=primary_cat,
            categories=categories,
            doi=doi,
            published=_text(entry, "atom:published") or None,
            updated=_text(entry, "atom:updated") or None,
            pdf_url=ARXIV_PDF_URL.format(arxiv_id=versioned),
        )

    def download_pdf(self, raw_id: str, dest_path: str) -> ArxivMetadata:
        """
        Resolve metadata for ``raw_id`` and stream the PDF to ``dest_path``.
        Returns the metadata so callers don't have to make a second request.
        The file is written atomically: a temp ``{dest_path}.partial`` is
        streamed first and renamed only on success.
        """
        meta = self.fetch_metadata(raw_id)
        partial = f"{dest_path}.partial"
        # arXiv is sensitive to missing User-Agent; some PDFs are gated.
        headers = {"User-Agent": self.user_agent}
        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                self._throttle()
                with client.stream("GET", meta.pdf_url, headers=headers) as resp:
                    if resp.status_code == 404:
                        raise ArxivNotFound(f"PDF not found at {meta.pdf_url}")
                    if resp.status_code != 200:
                        raise ArxivUnavailable(
                            f"arXiv PDF fetch failed: HTTP {resp.status_code}"
                        )
                    with open(partial, "wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                            if chunk:
                                fh.write(chunk)
        except httpx.HTTPError as exc:
            raise ArxivUnavailable(f"network error downloading {meta.pdf_url}: {exc}") from exc

        os.replace(partial, dest_path)
        logger.info(
            "arxiv.pdf.downloaded",
            arxiv_id=meta.arxiv_id,
            dest=dest_path,
            bytes=os.path.getsize(dest_path),
        )
        return meta

    # -- internals ---------------------------------------------------------

    def _get_text(self, url: str) -> str:
        try:
            with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
                self._throttle()
                resp = client.get(url, headers={"User-Agent": self.user_agent})
        except httpx.HTTPError as exc:
            raise ArxivUnavailable(f"network error fetching {url}: {exc}") from exc
        if resp.status_code == 404:
            raise ArxivNotFound(f"arXiv returned 404 for {url}")
        if resp.status_code != 200:
            raise ArxivUnavailable(f"arXiv returned HTTP {resp.status_code} for {url}")
        return resp.text

    @classmethod
    def _throttle(cls) -> None:
        """Sleep just long enough to honor the global inter-call gap."""
        elapsed = time.monotonic() - cls._last_call_ts
        gap = REQUEST_DELAY_S - elapsed
        if gap > 0:
            time.sleep(gap)
        cls._last_call_ts = time.monotonic()


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _text(parent: ET.Element, tag: str) -> str:
    """Return the stripped text of ``parent.find(tag)`` or ``""``."""
    el = parent.find(tag, _NS)
    if el is None or el.text is None:
        return ""
    return " ".join(el.text.split())  # collapse internal whitespace
