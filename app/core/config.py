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

    # Qdrant
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: Optional[str] = None
    QDRANT_COLLECTION_NAME: str = "documents"
    QDRANT_DISTANCE_METRIC: str = "cosine"  # "cosine" | "euclid" | "dot"
    QDRANT_BATCH_SIZE: int = 128

    # Neo4j  (Phase 8)
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    NEO4J_DATABASE: str = "neo4j"

    # LLM
    OPENROUTER_API_KEY: Optional[str] = "sk-or-v1-your-key-here"
    OLLAMA_BASE_URL: str = "http://ollama:11434"
    LLM_PROVIDER: str = "openrouter" # ollama or openrouter
    LLM_MODEL: str = "meta-llama/llama-3-8b-instruct:free" # e.g. "llama3" for ollama or "mistralai/mistral-7b-instruct" for openrouter

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