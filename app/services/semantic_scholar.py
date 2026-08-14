"""
Semantic Scholar citation enrichment.

Where OpenAlex enriches the *uploaded* paper (one paper, high-fidelity
metadata), this client enriches the papers *cited by* the uploaded
paper (potentially hundreds, lower-fidelity metadata). The use case is
Tier 1b of the cross-document context pyramid: when a user uploads
Paper A, we want the stub nodes for the papers Paper A cites to carry
more than just whatever the regex citation extractor managed to parse
from the reference list — we want abstract, authors, year, venue,
citation counts, and S2's TLDR so that queries about cited works can
return real context instead of "this is a stub."

The S2 batch endpoint (``POST /graph/v1/paper/batch``) accepts up to
500 paper IDs per call and returns the requested fields for each. We
send all lookable citations in a single batch to minimize round-trips.

Rate limiting
-------------
S2's free tier (no API key) is **aggressive**: roughly 1-2 requests
per hour per IP, not the "1 req/s" their docs imply. Without an API
key this client will get 429'd after the first call. With a free API
key (https://www.semanticscholar.org/product/api#api-key-form) the
limit is 100 req/s, which is more than enough for any realistic
ingestion workload.

S2 does **not** send ``Retry-After`` or ``X-RateLimit-*`` headers on
429, so the client uses blind exponential backoff (1s, 2s, 4s) and
gives up after 3 attempts — returning an empty result so the pipeline
continues with sparse stubs.

Every failure mode — network error, 429 after retries, 5xx, garbage
JSON — returns an empty dict for that paper ID. Enrichment is strictly
additive; a paper S2 has never heard of must still ingest.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# S2 batch endpoint accepts at most 500 IDs per call.
# https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_paper_multiple
S2_BATCH_LIMIT = 500

# Fields we request from S2. Every field becomes a stub node property
# (see _s2_record_to_stub_props for the mapping).
S2_FIELDS = (
    "title,abstract,authors,year,venue,citationCount,"
    "influentialCitationCount,tldr,externalIds"
)

# Exponential backoff schedule for 429/5xx (seconds).
# S2 doesn't send Retry-After, so we guess. 3 attempts total.
RETRY_DELAYS = (1.0, 2.0, 4.0)


class SemanticScholarClient:
    """Thin, failure-tolerant Semantic Scholar batch API client.

    Stateless across calls; safe to instantiate per-task. The client
    is injected into ``PaperIngestionPipeline`` the same way
    ``OpenAlexClient`` is.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
        base_url: str = "https://api.semanticscholar.org",
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.SEMANTIC_SCHOLAR_API_KEY
        self.timeout_s = timeout_s if timeout_s is not None else settings.SEMANTIC_SCHOLAR_TIMEOUT_S
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout_s)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_citations(
        self, citations: Sequence[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Enrich a list of normalized citation dicts with S2 metadata.

        Args:
            citations: output of ``CitationNormalizer.normalize_list()``.
                Each dict may have ``doi``, ``arxiv_id``, ``title``,
                ``year``, ``authors``, ``ref_id``.

        Returns:
            dict mapping each citation's lookup key to an enrichment
            dict. The lookup key is:

                * ``"doi:{doi}"``  if the citation has a DOI
                * ``"arxiv:{arxiv_id}"``  elif it has an arXiv ID
                * ``"title:{lowercased_title}"``  elif it has a title
                * (skipped entirely if none of the above)

            The enrichment dict contains the S2 fields that should be
            merged into the citation before the graph builder creates
            the stub node. Fields that S2 didn't return or that
            duplicate what the extractor already had are omitted.

            Citations S2 couldn't find (404 / null response) are
            absent from the returned dict — the caller falls back to
            the original sparse citation.
        """
        # 1. Build the list of (lookup_key, s2_id) pairs for citations
        #    we can actually look up, deduplicating by lookup_key.
        lookups: List[tuple[str, str]] = []
        seen_keys: set[str] = set()
        for cit in citations:
            s2_id, key = self._citation_to_s2_id(cit)
            if s2_id is None or key in seen_keys:
                continue
            seen_keys.add(key)
            lookups.append((key, s2_id))

        if not lookups:
            logger.info("s2_enrich.no_lookable_citations", total=len(citations))
            return {}

        # 2. Batch-fetch in chunks of S2_BATCH_LIMIT.
        #    One round-trip for most papers; two only for 500+ ref surveys.
        s2_results: Dict[str, Dict[str, Any]] = {}
        for chunk_start in range(0, len(lookups), S2_BATCH_LIMIT):
            chunk = lookups[chunk_start : chunk_start + S2_BATCH_LIMIT]
            keys = [k for k, _ in chunk]
            s2_ids = [sid for _, sid in chunk]
            batch = self._fetch_batch(s2_ids)
            for key, s2_id, s2_record in zip(keys, s2_ids, batch):
                if s2_record is None:
                    continue  # S2 returned null — paper not in their corpus
                enrichment = self._s2_record_to_enrichment(s2_record, s2_id)
                if enrichment:
                    s2_results[key] = enrichment

        found = len(s2_results)
        total = len(lookups)
        logger.info(
            "s2_enrich.complete",
            lookable=total,
            found=found,
            missed=total - found,
            hit_rate=round(found / total, 3) if total else 0.0,
        )
        return s2_results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _citation_to_s2_id(
        self, citation: Dict[str, Any]
    ) -> tuple[Optional[str], str]:
        """Return ``(s2_id, lookup_key)`` for a citation, or ``(None, "")``.

        S2 accepts paper IDs in these forms (in priority order):
            * ``"DOI:{doi}"``        — most reliable
            * ``"ARXIV:{arxiv_id}"`` — second best
            * ``"{corpus_id}"``      — S2's internal ID (we don't have it)

        We deliberately do NOT look up by title via S2's search
        endpoint. Title search is what OpenAlex already does for the
        uploaded paper; for cited papers the regex extractor's title
        is too noisy (OCR damage, truncation) to trust as a lookup key.
        If a citation has neither DOI nor arXiv ID, it stays sparse.
        """
        doi = (citation.get("doi") or "").strip()
        if doi:
            return f"DOI:{doi}", f"doi:{doi.lower()}"

        arxiv_id = (citation.get("arxiv_id") or "").strip()
        if arxiv_id:
            return f"ARXIV:{arxiv_id}", f"arxiv:{arxiv_id.lower()}"

        return None, ""

    def _fetch_batch(self, s2_ids: List[str]) -> List[Optional[Dict[str, Any]]]:
        """Fetch a batch of papers from S2. Returns a list aligned with
        ``s2_ids``; entries are dicts for found papers, ``None`` for
        papers S2 doesn't have.

        Retries on 429 and 5xx with exponential backoff. After
        ``RETRY_DELAYS`` is exhausted, returns a list of Nones (graceful
        degradation — stubs stay sparse).
        """
        if not s2_ids:
            return []

        url = f"{self.base_url}/graph/v1/paper/batch"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        last_error: Optional[str] = None
        for attempt, delay in enumerate(RETRY_DELAYS, start=1):
            try:
                resp = self.client.post(
                    url,
                    params={"fields": S2_FIELDS},
                    json={"ids": s2_ids},
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                last_error = f"network: {exc}"
                logger.warning(
                    "s2_enrich.network_error",
                    attempt=attempt,
                    batch_size=len(s2_ids),
                    error=str(exc),
                )
                time.sleep(delay)
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError as exc:
                    last_error = f"json_decode: {exc}"
                    logger.warning("s2_enrich.bad_json", error=str(exc))
                    return [None] * len(s2_ids)

                # S2 returns a list aligned with the input IDs. If the
                # input had N IDs, the response has N entries (some
                # may be null). Defensive: pad/truncate to match.
                if not isinstance(data, list):
                    logger.warning("s2_enrich.unexpected_shape", shape=type(data).__name__)
                    return [None] * len(s2_ids)
                if len(data) < len(s2_ids):
                    data = data + [None] * (len(s2_ids) - len(data))
                return data[: len(s2_ids)]

            if resp.status_code == 429:
                last_error = "rate_limited"
                logger.warning(
                    "s2_enrich.rate_limited",
                    attempt=attempt,
                    retry_in_s=delay,
                    hint="get an API key at https://www.semanticscholar.org/product/api#api-key-form",
                )
                time.sleep(delay)
                continue

            if 500 <= resp.status_code < 600:
                last_error = f"http_{resp.status_code}"
                logger.warning(
                    "s2_enrich.server_error",
                    attempt=attempt,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                time.sleep(delay)
                continue

            # 4xx other than 429 — don't retry, log and return empty.
            logger.warning(
                "s2_enrich.client_error",
                status=resp.status_code,
                body=resp.text[:200],
            )
            return [None] * len(s2_ids)

        logger.warning(
            "s2_enrich.exhausted_retries",
            batch_size=len(s2_ids),
            last_error=last_error,
        )
        return [None] * len(s2_ids)

    @staticmethod
    def _s2_record_to_enrichment(
        record: Dict[str, Any], s2_id: str
    ) -> Dict[str, Any]:
        """Convert a raw S2 paper record into a flat enrichment dict
        ready to merge into a citation dict.

        The ``tldr`` field is stored as a dict with provenance (model +
        source) rather than a bare string, so any UI that surfaces it
        can label it as S2's auto-generated summary.
        """
        enrichment: Dict[str, Any] = {}

        # s2_paper_id is always present; useful for future "fetch full
        # S2 record" operations without re-resolving the DOI/arXiv ID.
        s2_paper_id = record.get("paperId")
        if s2_paper_id:
            enrichment["s2_paper_id"] = s2_paper_id

        abstract = record.get("abstract")
        if abstract:
            enrichment["abstract"] = abstract

        year = record.get("year")
        if year is not None:
            enrichment["year"] = year

        venue = record.get("venue")
        if venue:
            enrichment["venue"] = venue

        citation_count = record.get("citationCount")
        if citation_count is not None:
            enrichment["citation_count"] = citation_count

        influential = record.get("influentialCitationCount")
        if influential is not None:
            enrichment["influential_citation_count"] = influential

        # Authors: S2 returns [{"name": "...", "authorId": "..."}, ...]
        # We extract just the names to match the existing stub property
        # shape (author_names is a list of strings).
        authors = record.get("authors")
        if isinstance(authors, list) and authors:
            names = [a.get("name", "") for a in authors if a.get("name")]
            if names:
                enrichment["author_names"] = names

        # TLDR: stored with provenance so consumers know it's
        # auto-generated and by which model.
        tldr = record.get("tldr")
        if isinstance(tldr, dict) and tldr.get("text"):
            enrichment["tldr"] = {
                "text": tldr["text"],
                "model": tldr.get("model", "unknown"),
                "source": "semantic_scholar",
            }

        # title: S2's title is usually cleaner than the regex
        # extractor's, but we only override if the extractor didn't
        # find one (the extractor's title came from the citing paper's
        # reference list and is lower priority).
        title = record.get("title")
        if title:
            enrichment["s2_title"] = title  # caller decides whether to use

        return enrichment
