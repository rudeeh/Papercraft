"""
Graph Query API Routes (Phase 16)

Exposes the GraphRAG retrieval + answer-generation stack (Phases 11-15)
through FastAPI:

    POST /graph-query           -- ask a question, get a grounded answer
    GET  /papers/{paper_id}/graph -- fetch a single paper's knowledge graph

Kept in its own module (rather than a routes/ package) since the existing
codebase uses a flat app/api/routes.py -- this file defines a second
APIRouter that main.py mounts alongside it under the same "/api/v1" prefix.
"""

from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.storage.neo4j_client import Neo4jClient
from app.storage.qdrant_client import QdrantClientWrapper
from app.storage.vector_repository import VectorRepository
from app.storage.graph_repository import GraphRepository
from app.embeddings.embedder import EmbeddingService
from app.retrieval.graph_retriever import GraphRetriever
from app.retrieval.vector_retriever import VectorRetriever
from app.retrieval.citation_expander import CitationExpander
from app.retrieval.hybrid_retriever import HybridRetriever
from app.llm.answer_generator import AnswerGenerator

logger = structlog.get_logger()

router = APIRouter()


# ======================================================================
# Request / response models
# ======================================================================

class GraphQueryRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v


class SourcePaper(BaseModel):
    paper_id: str
    title: Optional[str] = None


class GraphQueryResponse(BaseModel):
    answer: str
    sources: List[SourcePaper]
    retrieval_trace: Dict[str, Any]


# ======================================================================
# Lazily-connected singletons
#
# Mirrors the graceful-connect pattern already used by
# app/worker/tasks.py, except here a connection failure is surfaced to the
# caller as a 503 rather than silently skipped -- a live query has no
# "skip this part of the pipeline" fallback the way ingestion does.
# ======================================================================

_neo4j_client: Optional[Neo4jClient] = None
_vector_repo: Optional[VectorRepository] = None


def get_neo4j_client() -> Neo4jClient:
    global _neo4j_client
    if _neo4j_client is None:
        client = Neo4jClient(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
        client.connect()
        _neo4j_client = client
    return _neo4j_client


def get_vector_repo() -> VectorRepository:
    global _vector_repo
    if _vector_repo is None:
        qdrant = QdrantClientWrapper(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        qdrant.connect()
        embedder = EmbeddingService(
            provider=settings.EMBEDDING_PROVIDER,
            model_name=settings.EMBEDDING_MODEL,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
        )
        _vector_repo = VectorRepository(
            qdrant, embedder, collection_name=settings.QDRANT_COLLECTION_NAME
        )
    return _vector_repo


def get_graph_repository() -> GraphRepository:
    return GraphRepository(get_neo4j_client())


def get_hybrid_retriever() -> HybridRetriever:
    client = get_neo4j_client()
    return HybridRetriever(
        graph_retriever=GraphRetriever(client),
        vector_retriever=VectorRetriever(get_vector_repo()),
        citation_expander=CitationExpander(client),
    )


def get_answer_generator() -> AnswerGenerator:
    return AnswerGenerator()


# ======================================================================
# Routes
# ======================================================================

@router.post("/graph-query", response_model=GraphQueryResponse)
async def graph_query(
    body: GraphQueryRequest,
    hybrid_retriever: HybridRetriever = Depends(get_hybrid_retriever),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
):
    try:
        retrieval_result = hybrid_retriever.retrieve(body.query, top_k=body.top_k)
    except Exception as exc:
        logger.error("graph_query_retrieval_failed", query=body.query, error=str(exc))
        raise HTTPException(
            status_code=503, detail="Graph/vector store is currently unavailable"
        ) from exc

    generated = answer_generator.generate(body.query, retrieval_result)

    return {
        "answer": generated["answer"],
        "sources": generated["sources"],
        "retrieval_trace": {
            "query_type": retrieval_result["query_type"],
            "graph_facts": generated["graph_facts_used"],
            "vector_results": retrieval_result["vector_results"],
            "citation_paths": retrieval_result["citation_paths"],
            "source_paper_ids": retrieval_result["source_paper_ids"],
            "confidence_notes": generated["confidence_notes"],
        },
    }


@router.get("/papers/{paper_id}/graph")
async def get_paper_graph(
    paper_id: str,
    graph_repo: GraphRepository = Depends(get_graph_repository),
):
    try:
        graph = graph_repo.get_paper_graph(paper_id)
    except Exception as exc:
        logger.error("paper_graph_fetch_failed", paper_id=paper_id, error=str(exc))
        raise HTTPException(
            status_code=503, detail="Graph store is currently unavailable"
        ) from exc

    if not graph:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")

    return {
        "paper_id": paper_id,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
    }
