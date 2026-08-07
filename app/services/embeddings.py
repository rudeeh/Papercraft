from sentence_transformers import SentenceTransformer
from app.core.config import settings
import structlog

logger = structlog.get_logger()

_model = None

def get_model():
    global _model
    if _model is None:
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
