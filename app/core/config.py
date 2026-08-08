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

    # PostgreSQL -- users, ingestion jobs, extraction drafts, attestations,
    # audit log. Everything that is *about* the graph rather than in it.
    POSTGRES_URI: str = "postgresql+psycopg://papercraft:papercraft@postgres:5432/papercraft"
    DB_ECHO: bool = False

    # Object storage (MinIO / any S3-compatible endpoint) -- canonical PDFs.
    # Disabled by default so a plain `docker compose up` still works with
    # only the local UPLOAD_DIR, exactly as before.
    OBJECT_STORE_ENABLED: bool = False
    MINIO_ENDPOINT: str = "http://minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET_PDFS: str = "papercraft-pdfs"
    MINIO_REGION: str = "us-east-1"

    # Auth (OAuth2 password flow + JWT bearer)
    SECRET_KEY: str = "dev-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    # When False, curation endpoints accept unauthenticated callers and
    # attribute their actions to the anonymous user -- single-player mode
    # (README design principle 4) works without anyone signing up.
    AUTH_REQUIRED: bool = False

    # OpenAlex metadata enrichment
    OPENALEX_ENABLED: bool = True
    OPENALEX_API_URL: str = "https://api.openalex.org"
    OPENALEX_MAILTO: Optional[str] = None  # joins OpenAlex's polite pool
    OPENALEX_TIMEOUT_S: float = 10.0

    # Extraction + confidence routing
    # "heuristic" -- regex/deterministic extractors only (default, no LLM cost)
    # "llm"       -- LLM extraction only
    # "hybrid"    -- heuristic first, LLM as an additive second pass
    EXTRACTION_PROVIDER: str = "heuristic"
    EXTRACTION_MODEL_VERSION: str = "extraction-v0.2.0"
    CONFIDENCE_AUTO_INSERT: float = 0.85  # >= this goes straight into the graph
    CONFIDENCE_DRAFT: float = 0.50        # >= this queues for review; below is dropped
    ATTESTATION_PROMOTE_SCORE: int = 2    # net upvotes needed to promote a draft

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