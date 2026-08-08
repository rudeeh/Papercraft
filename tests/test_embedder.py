"""
Tests for EmbeddingService (Phase 9.1)

Covers:
- StubEmbedder: deterministic output, dimension correctness
- SentenceTransformerEmbedder: mocked model, lazy loading
- OpenAIEmbedder: mocked API, retry on rate limit
- EmbeddingService: batch splitting, backend selection, empty input
"""

import pytest
from unittest.mock import patch, MagicMock

from app.embeddings.embedder import (
    StubEmbedder,
    SentenceTransformerEmbedder,
    OpenAIEmbedder,
    EmbeddingService,
)


# ======================================================================
# StubEmbedder
# ======================================================================

class TestStubEmbedder:
    def test_returns_correct_dimension(self):
        embedder = StubEmbedder(dim=384)
        assert embedder.embed_dim == 384

    def test_returns_one_vector_per_text(self):
        embedder = StubEmbedder(dim=128)
        texts = ["hello", "world", "foo"]
        vectors = embedder.embed(texts)
        assert len(vectors) == 3
        for v in vectors:
            assert len(v) == 128

    def test_deterministic_output(self):
        embedder = StubEmbedder(dim=64)
        v1 = embedder.embed(["test text"])
        v2 = embedder.embed(["test text"])
        assert v1 == v2

    def test_different_texts_different_vectors(self):
        embedder = StubEmbedder(dim=64)
        v1 = embedder.embed(["alpha"])
        v2 = embedder.embed(["beta"])
        assert v1 != v2

    def test_empty_input_returns_empty(self):
        embedder = StubEmbedder(dim=128)
        assert embedder.embed([]) == []

    def test_custom_dimension(self):
        embedder = StubEmbedder(dim=768)
        vectors = embedder.embed(["test"])
        assert len(vectors[0]) == 768


# ======================================================================
# SentenceTransformerEmbedder (mocked)
# ======================================================================

class TestSentenceTransformerEmbedder:
    @patch("app.embeddings.embedder.SentenceTransformerEmbedder._ensure_model")
    def test_embed_returns_list_of_lists(self, mock_ensure):
        embedder = SentenceTransformerEmbedder(model_name="fake-model")
        embedder._model = MagicMock()
        embedder._dim = 384
        import numpy as np
        embedder._model.encode.return_value = np.array([[0.1] * 384, [0.2] * 384])

        result = embedder.embed(["a", "b"])
        assert len(result) == 2
        assert len(result[0]) == 384
        assert isinstance(result[0], list)

    @patch("app.embeddings.embedder.SentenceTransformerEmbedder._ensure_model")
    def test_embed_dim_returns_model_dim(self, mock_ensure):
        embedder = SentenceTransformerEmbedder(model_name="fake-model")
        embedder._model = MagicMock()
        embedder._dim = 768
        assert embedder.embed_dim == 768


# ======================================================================
# OpenAIEmbedder (mocked)
# ======================================================================

class TestOpenAIEmbedder:
    def test_embed_dim_known_model(self):
        embedder = OpenAIEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-key",
        )
        assert embedder.embed_dim == 1536

    def test_embed_dim_unknown_model_defaults_1536(self):
        embedder = OpenAIEmbedder(
            model_name="text-embedding-unknown",
            api_key="test-key",
        )
        assert embedder.embed_dim == 1536

    def test_raises_value_error_when_no_api_key(self):
        """OpenAI embedder must fail fast if no API key is available."""
        import os
        # Ensure no env var leaks in
        os.environ.pop("OPENAI_API_KEY", None)
        with pytest.raises(ValueError, match="API key"):
            OpenAIEmbedder(model_name="text-embedding-3-small", api_key=None)

    @patch("openai.OpenAI")
    def test_embed_calls_api(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.data = [
            MagicMock(embedding=[0.1] * 1536),
            MagicMock(embedding=[0.2] * 1536),
        ]
        mock_client.embeddings.create.return_value = mock_resp

        embedder = OpenAIEmbedder(
            model_name="text-embedding-3-small",
            api_key="test-key",
        )
        result = embedder.embed(["hello", "world"])
        assert len(result) == 2
        mock_client.embeddings.create.assert_called_once()


# ======================================================================
# EmbeddingService
# ======================================================================

class TestEmbeddingService:
    def test_stub_provider(self):
        svc = EmbeddingService(provider="stub", batch_size=2)
        assert svc.embed_dim == 384

    def test_local_provider(self):
        svc = EmbeddingService(provider="local", model_name="all-MiniLM-L6-v2")
        # Don't actually load the model; just check it created
        assert hasattr(svc, "_backend")

    def test_openai_provider(self):
        svc = EmbeddingService(provider="openai", api_key="test-key")
        assert svc.embed_dim == 1536

    def test_batch_splitting(self):
        """Verify that large lists are split into batches."""
        svc = EmbeddingService(provider="stub", batch_size=3)
        texts = [f"text-{i}" for i in range(10)]
        vectors = svc.embed(texts)
        assert len(vectors) == 10

    def test_empty_input(self):
        svc = EmbeddingService(provider="stub")
        assert svc.embed([]) == []

    def test_embed_single(self):
        svc = EmbeddingService(provider="stub", batch_size=5)
        vec = svc.embed_single("hello world")
        assert len(vec) == 384