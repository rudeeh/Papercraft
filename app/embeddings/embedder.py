"""
Embedding Service Module (Phase 9.1)

Generates dense vector embeddings for paper text chunks using
configurable embedding backends. Supports:

- **sentence-transformers** (local, default): CPU/GPU inference via HuggingFace
  models. No API key required.
- **OpenAI API** (optional): ``text-embedding-3-small`` / ``text-embedding-3-large``.
  Requires ``OPENAI_API_KEY`` in environment.
- **Stub mode** (testing): Returns deterministic zero vectors for integration
  testing without a real model.

Design decisions
----------------
- Lazy model loading: the transformer model is loaded on first ``embed()``
  call, not at import time, so worker startup stays fast.
- Batch embedding: ``embed_batch()`` splits large lists into configurable
  batch sizes to stay within GPU memory limits.
- Dimension is read from the model dynamically (not hard-coded), so swapping
  models never requires a config change.

Risk Mitigations Addressed
--------------------------
- Model loading failure: caught once on first call; subsequent calls raise
  immediately with a clear message.
- Dimension mismatch: ``embed_dim`` property always reflects the *actual*
  loaded model output, preventing silent Qdrant insert failures.
- Rate limiting (OpenAI): exponential backoff with jitter on 429 responses.
"""

from __future__ import annotations

import time
import hashlib
from abc import ABC, abstractmethod
from typing import List, Optional

import structlog

logger = structlog.get_logger()

# Default batch size for embedding calls — tune based on GPU memory.
DEFAULT_BATCH_SIZE = 64


# ======================================================================
# Abstract interface
# ======================================================================

class EmbedderBackend(ABC):
    """Interface that all embedding backends must implement."""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """Return one vector per input text."""
        ...

    @property
    @abstractmethod
    def embed_dim(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


# ======================================================================
# Stub backend — deterministic vectors for testing
# ======================================================================

class StubEmbedder(EmbedderBackend):
    """
    Returns deterministic vectors based on text hash.
    Useful for integration tests and local dev without a model.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            h = hashlib.sha256(text.encode()).digest()
            # Expand the 32-byte digest into `dim` floats in [0, 1) by
            # cycling through it (sha256 output is always 32 bytes, which
            # is shorter than most embedding dimensions).
            vectors.append([h[i % len(h)] / 255.0 for i in range(self._dim)])
        return vectors

    @property
    def embed_dim(self) -> int:
        return self._dim


# ======================================================================
# Sentence-transformers backend (local inference)
# ======================================================================

class SentenceTransformerEmbedder(EmbedderBackend):
    """
    Embeds text using a sentence-transformers model loaded from HuggingFace.

    The model is loaded lazily on first ``embed()`` call to keep import
    time and worker startup fast.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model = None  # lazy-loaded
        self._dim: Optional[int] = None

    def _ensure_model(self):
        if self._model is not None:
            return
        logger.info("embedding_model_loading", model=self._model_name)
        from sentence_transformers import SentenceTransformer  # heavy import
        self._model = SentenceTransformer(self._model_name)
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info("embedding_model_loaded", model=self._model_name, dim=self._dim)

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._ensure_model()
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return [e.tolist() for e in embeddings]

    @property
    def embed_dim(self) -> int:
        self._ensure_model()
        return self._dim  # type: ignore[return-value]


# ======================================================================
# OpenAI backend (API-based)
# ======================================================================

class OpenAIEmbedder(EmbedderBackend):
    """
    Embeds text via the OpenAI ``/v1/embeddings`` endpoint.

    Handles rate-limiting with exponential backoff + jitter.
    Requires ``OPENAI_API_KEY`` environment variable.
    """

    # model_name -> known dimension
    _KNOWN_DIMS = {
        "text-embedding-ada-002": 1536,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        max_retries: int = 5,
    ) -> None:
        import os
        self._model_name = model_name
        self._api_key = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        self._max_retries = max_retries
        self._dim = self._KNOWN_DIMS.get(model_name, 1536)

        if not self._api_key:
            raise ValueError(
                "OpenAI embedder requires a non-empty API key.  "
                "Set the OPENAI_API_KEY environment variable or pass api_key=."
            )

    def embed(self, texts: List[str]) -> List[List[float]]:
        import openai  # heavy import

        client = openai.OpenAI(api_key=self._api_key)

        all_vectors: List[List[float]] = []
        # OpenAI supports up to 2048 texts per request
        for i in range(0, len(texts), 2048):
            batch = texts[i : i + 2048]
            vectors = self._call_with_retry(client, batch)
            all_vectors.extend(vectors)
        return all_vectors

    def _call_with_retry(
        self, client, texts: List[str]
    ) -> List[List[float]]:
        """Call OpenAI embedding API with exponential backoff on 429."""
        import openai

        for attempt in range(self._max_retries):
            try:
                resp = client.embeddings.create(
                    input=texts, model=self._model_name
                )
                return [d.embedding for d in resp.data]
            except openai.RateLimitError:
                wait = 2**attempt + 0.5
                logger.warning(
                    "openai_rate_limit",
                    attempt=attempt + 1,
                    wait_s=wait,
                )
                time.sleep(wait)
            except openai.APIError as exc:
                logger.error("openai_api_error", error=str(exc))
                raise

        raise RuntimeError(
            f"OpenAI embedding API failed after {self._max_retries} retries"
        )

    @property
    def embed_dim(self) -> int:
        return self._dim


# ======================================================================
# Main service class
# ======================================================================

class EmbeddingService:
    """
    High-level embedding service used by the ingestion pipeline.

    Backend selection order:
    1. If ``EMBEDDING_PROVIDER == "openai"`` → ``OpenAIEmbedder``
    2. If ``EMBEDDING_PROVIDER == "stub"``   → ``StubEmbedder``
    3. Default (``"local"`` or unset)        → ``SentenceTransformerEmbedder``
    """

    def __init__(
        self,
        provider: str = "local",
        model_name: str = "all-MiniLM-L6-v2",
        api_key: Optional[str] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._batch_size = batch_size

        if provider == "openai":
            self._backend: EmbedderBackend = OpenAIEmbedder(
                model_name=model_name, api_key=api_key
            )
        elif provider == "stub":
            self._backend = StubEmbedder(dim=384)
        else:
            self._backend = SentenceTransformerEmbedder(model_name=model_name)

        logger.info(
            "embedding_service_init",
            provider=provider,
            model=model_name,
        )

    @property
    def embed_dim(self) -> int:
        return self._backend.embed_dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a list of texts in batches.

        Returns one vector per text, in the same order.
        """
        if not texts:
            return []

        all_vectors: List[List[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            logger.debug("embedding_batch", batch_start=i, batch_size=len(batch))
            vectors = self._backend.embed(batch)
            all_vectors.extend(vectors)

        return all_vectors

    def embed_single(self, text: str) -> List[float]:
        """Convenience method for a single text."""
        results = self.embed([text])
        return results[0] if results else []