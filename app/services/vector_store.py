from qdrant_client import QdrantClient
from qdrant_client.http import models
from app.core.config import settings
import structlog
import uuid

logger = structlog.get_logger()

_client = None

def get_client():
    global _client
    if _client is None:
        _client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY
        )
    return _client

def init_collection():
    client = get_client()
    collection_name = settings.QDRANT_COLLECTION_NAME
    
    # Check if exists
    collections = client.get_collections().collections
    exists = any(c.name == collection_name for c in collections)
    
    if not exists:
        logger.info(f"Creating collection {collection_name}")
        # Dimension depends on model. all-MiniLM-L6-v2 is 384.
        # We should ideally get this from the model, but hardcoding for now or config.
        # SentenceTransformer default is 384.
        
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=384, 
                distance=models.Distance.COSINE
            )
        )

def upsert_vectors(collection_name: str, embeddings_data: list):
    """
    embeddings_data: list of dicts { "vector": ..., "payload": ... }
    """
    client = get_client()
    
    # Ensure collection exists
    init_collection()
    
    points = []
    for item in embeddings_data:
        points.append(models.PointStruct(
            id=str(uuid.uuid4()), # Generate random UUID for the point
            vector=item["vector"],
            payload=item["payload"]
        ))
        
    # Batch upsert
    # Qdrant client handles batching but explicit batching is good for huge lists.
    # For now, just upsert all.
    
    if points:
        client.upsert(
            collection_name=collection_name,
            points=points
        )
        logger.info(f"Upserted {len(points)} points to {collection_name}")

def search_vectors(query_vector: list, top_k: int = 5, doc_id: str = None):
    client = get_client()
    collection_name = settings.QDRANT_COLLECTION_NAME
    
    query_filter = None
    if doc_id:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="doc_id",
                    match=models.MatchValue(value=doc_id)
                )
            ]
        )
    
    results = client.search(
        collection_name=collection_name,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=top_k
    )
    
    return results
