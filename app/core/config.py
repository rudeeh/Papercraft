from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # Weaviate (vector store — ADR: Weaviate, replacing Qdrant)
    WEAVIATE_URL: str = "http://weaviate:8080"
    WEAVIATE_GRPC_PORT: int = 50051
    WEAVIATE_API_KEY: Optional[str] = None
    WEAVIATE_COLLECTION_NAME: str = "Documents"
    WEAVIATE_DISTANCE_METRIC: str = "cosine"  # "cosine" | "euclid" | "dot"
    WEAVIATE_BATCH_SIZE: int = 128

    # Neo4j  (Phase 8)
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"

    # LLM (OpenRouter only -- see docs/decisions.md)
    # Server-side key is optional: if unset, requests must supply their own
    # OpenRouter key (e.g. entered in the frontend header) or the LLM calls
    # fail with a clear "no API key" error rather than a silent one.
    OPENROUTER_API_KEY: Optional[str] = None
    LLM_MODEL: str = "openai/gpt-oss-20b:free"

    # Embeddings (Phase 9)
    EMBEDDING_PROVIDER: str = "local"   # "local" | "openai" | "stub"
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 64

    # OpenAI (for OpenAI embedder / LLM)
    OPENAI_API_KEY: Optional[str] = None

    # RAG Config
    RAG_TOP_K: int = 5
    MAX_CONTEXT_TOKENS: int = 4096
    CHUNK_TOKENS: int = 500
    CHUNK_OVERLAP_TOKENS: int = 50

    # Celery
    CELERY_CONCURRENCY: int = 2
    
    # Upload
    MAX_UPLOAD_MB: int = 50
    UPLOAD_DIR: str = "/app/uploads"

settings = Settings()