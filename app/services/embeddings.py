"""
Lightweight embedding-model loader used by the chat / RAG endpoints.

The ``SentenceTransformer`` import is deliberately deferred into
``get_model()`` rather than done at module load time. This matters
because the CI workflow (.github/workflows/ci.yml) installs
requirements.txt *minus* ``sentence-transformers`` to avoid pulling
torch (~2 GB) that no unit test exercises — tests patch
``app.services.embeddings.get_model`` instead. With an eager import at
module scope, the patch never gets applied: the ``AttributeError:
module 'app.services' has no attribute 'embeddings'`` happens first
because Python fails to import the module at all.

Lazy importing matches the same pattern already used in
``app/embeddings/embedder.py`` (see its ``_ensure_model`` method) and
in ``app/services/llm.py``.
"""

from app.core.config import settings
import structlog

logger = structlog.get_logger()

_model = None

def get_model():
    global _model
    if _model is None:
        # Lazy import: sentence_transformers pulls torch (~2 GB). Tests
        # patch this function, so the import only happens when a real
        # request needs the model.
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
        _model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _model

def generate_embeddings(chunks: list):
    """
    Generates embeddings for a list of chunks.
    chunks: list of dicts with "text" and "metadata"
    Returns: list of (vector, payload)
    """
    model = get_model()
    texts = [c["text"] for c in chunks]
    
    if not texts:
        return []
        
    embeddings = model.encode(texts)
    
    results = []
    for i, emb in enumerate(embeddings):
        results.append({
            "vector": emb.tolist(),
            "payload": {
                "text": chunks[i]["text"],
                **chunks[i]["metadata"]
            }
        })
        
    return results
