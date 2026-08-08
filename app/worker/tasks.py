from app.worker.celery_app import celery_app
from celery.utils.log import get_task_logger
import os

from app.core.config import settings
from app.storage.neo4j_client import Neo4jClient
from app.storage.weaviate_client import WeaviateClientWrapper
from app.storage.vector_repository import VectorRepository
from app.embeddings.embedder import EmbeddingService
from app.pipeline.paper_ingestion_pipeline import PaperIngestionPipeline

logger = get_task_logger(__name__)


def _create_neo4j_client() -> "Neo4jClient | None":
    """
    Attempt to connect to Neo4j.  Returns None on failure so that
    the pipeline can still run the vector-RAG path without the graph.
    """
    try:
        client = Neo4jClient(
            uri=settings.NEO4J_URI,
            user=settings.NEO4J_USER,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
        client.connect()
        client.init_schema()
        logger.info("Neo4j connected and schema initialized")
        return client
    except Exception as exc:
        logger.warning(
            "Neo4j not available -- graph pipeline will be skipped: %s", exc
        )
        return None


def _create_vector_repo() -> "VectorRepository | None":
    """
    Attempt to connect to Weaviate.  Returns None on failure so that the
    pipeline can still run the graph path without vector storage.
    """
    try:
        weaviate_client = WeaviateClientWrapper(
            url=settings.WEAVIATE_URL,
            api_key=settings.WEAVIATE_API_KEY,
            grpc_port=settings.WEAVIATE_GRPC_PORT,
            batch_size=settings.WEAVIATE_BATCH_SIZE,
        )
        weaviate_client.connect()
        embedder = EmbeddingService(
            provider=settings.EMBEDDING_PROVIDER,
            model_name=settings.EMBEDDING_MODEL,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
        )
        logger.info("Weaviate connected")
        return VectorRepository(
            weaviate_client, embedder,
            collection_name=settings.WEAVIATE_COLLECTION_NAME
        )
    except Exception as exc:
        logger.warning(
            "Weaviate not available -- vector storage will be skipped: %s", exc
        )
        return None


def _create_openalex_client():
    """OpenAlex enricher, or None when enrichment is switched off."""
    if not settings.OPENALEX_ENABLED:
        return None
    try:
        from app.services.openalex import OpenAlexClient

        return OpenAlexClient()
    except Exception as exc:
        logger.warning("OpenAlex client unavailable -- enrichment skipped: %s", exc)
        return None


def _create_llm_extractor():
    """LLM extractor, or None unless EXTRACTION_PROVIDER asks for one."""
    if settings.EXTRACTION_PROVIDER not in ("llm", "hybrid"):
        return None
    try:
        from app.graph.llm_extractor import LLMEntityRelationExtractor

        return LLMEntityRelationExtractor()
    except Exception as exc:
        logger.warning("LLM extractor unavailable -- heuristic only: %s", exc)
        return None


def _create_draft_sink():
    """
    Callable that persists review-needed extractions, or None if PostgreSQL
    is unreachable. Returning None matters: the pipeline inserts everything
    directly when it has nowhere to queue, so a database outage costs
    review, not data.
    """
    try:
        from app.db.base import init_db

        if not init_db():
            return None
    except Exception as exc:
        logger.warning("PostgreSQL unavailable -- extractions will not be queued: %s", exc)
        return None

    def sink(paper_id: str, routed):
        from app.db.base import session_scope
        from app.services.curation import queue_drafts

        with session_scope() as session:
            return queue_drafts(session, paper_id, routed)

    return sink


def _record_job(doc_id: str, task_id: str, status: str, **fields) -> None:
    """Best-effort durable job record. Never raises into the task."""
    try:
        from app.db.base import session_scope
        from app.db.models import IngestionJob

        with session_scope() as session:
            job = (
                session.query(IngestionJob)
                .filter(IngestionJob.doc_id == doc_id, IngestionJob.task_id == task_id)
                .one_or_none()
            )
            if job is None:
                job = IngestionJob(doc_id=doc_id, task_id=task_id, status=status, **fields)
                session.add(job)
            else:
                job.status = status
                for key, value in fields.items():
                    setattr(job, key, value)
    except Exception as exc:
        logger.debug("Job record skipped (%s): %s", doc_id, exc)


@celery_app.task(bind=True, name="app.worker.tasks.process_pdf_task", max_retries=3)
def process_pdf_task(self, doc_id: str, file_path: str):
    """
    Process an uploaded PDF through the full ingestion pipeline.

    Pipeline:  PDF -> OCR -> Parse -> Citations -> Entities -> Relations
               -> Graph Build -> Neo4j -> Chunk -> Embed -> Weaviate

    If Neo4j is unreachable the graph steps are gracefully skipped and
    the vector-RAG path still completes.
    """
    try:
        logger.info("Starting processing for doc_id: %s", doc_id)
        self.update_state(state="PROCESSING", meta={"step": "INITIALIZING", "doc_id": doc_id})

        # --- Every collaborator below is optional; each returns None when
        #     its backing service is unavailable and the pipeline degrades. ---
        neo4j_client = _create_neo4j_client()
        vector_repo = _create_vector_repo()
        _record_job(doc_id, self.request.id, "processing", filename=os.path.basename(file_path))

        try:
            pipeline = PaperIngestionPipeline(
                neo4j_client=neo4j_client,
                vector_repo=vector_repo,
                openalex_client=_create_openalex_client(),
                llm_extractor=_create_llm_extractor(),
                draft_sink=_create_draft_sink(),
            )
            result = pipeline.process(paper_id=doc_id, file_path=file_path)
        finally:
            # Always clean up the Neo4j connection
            if neo4j_client is not None:
                try:
                    neo4j_client.close()
                except Exception:
                    pass

        # --- Build return value ---
        if result.status == "FAILED":
            _record_job(doc_id, self.request.id, "failed")
            return {
                "status": "failed",
                "doc_id": doc_id,
                "steps": [
                    {"step": s.step_name, "status": s.status.value, "error": s.error}
                    for s in result.steps
                ],
            }

        payload = {
            "status": "completed" if result.status == "COMPLETED" else "partial",
            "doc_id": doc_id,
            "chunks_count": result.vector_count,
            "citations_count": result.citation_count,
            "entities_count": result.entity_count,
            "relations_count": result.relation_count,
            "graph_nodes_count": result.graph_nodes_count,
            "graph_edges_count": result.graph_edges_count,
            "auto_inserted_count": result.auto_inserted_count,
            "queued_for_review_count": result.queued_for_review_count,
            "openalex_enriched": result.enriched,
            "pipeline_steps": [
                {
                    "step": s.step_name,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                }
                for s in result.steps
            ],
        }
        _record_job(
            doc_id, self.request.id, payload["status"], step_summary=payload["pipeline_steps"]
        )
        return payload

    except Exception as e:
        logger.error("Error processing PDF: %s", e)
        _record_job(doc_id, self.request.id, "failed", error=str(e))
        raise e