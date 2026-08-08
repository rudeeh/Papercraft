from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.api.graph_routes import router as graph_router
from app.core.config import settings
import structlog
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

logger = structlog.get_logger()

# Setup Rate Limiting
limiter = Limiter(key_func=get_remote_address)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    LEARNING POINT: Lifespan Events
    In modern FastAPI, the 'lifespan' context manager is the preferred way to handle
    startup and shutdown logic. It replaces the older @app.on_event("startup") and
    @app.on_event("shutdown") decorators.

    Everything before 'yield' runs on STARTUP.
    Everything after 'yield' runs on SHUTDOWN.
    """
    # STARTUP LOGIC
    logger.info("Application starting up...")

    # Setup directories for file uploads
    import os
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    # Pre-warm the embedding model. It's lazily loaded on first use by
    # design (keeps a plain worker/api boot fast when nothing needs it
    # yet), but that means the *first* /chat request after a fresh start
    # pays the multi-second model-load cost synchronously, inline in the
    # request -- long enough in practice to trip a client-side timeout,
    # which then looks like a network failure even though the server goes
    # on to complete the request fine (visible in the logs as a 200).
    # Loading it here instead, before the server starts accepting traffic,
    # means no real request ever pays that cost.
    import asyncio
    from app.services.embeddings import get_model
    try:
        await asyncio.to_thread(get_model)
        logger.info("Embedding model pre-warmed")
    except Exception as exc:
        logger.warning(f"Embedding model pre-warm failed (falls back to lazy load): {exc}")

    yield

    # SHUTDOWN LOGIC
    logger.info("Application shutting down...")

app = FastAPI(
    title="PDF RAG API",
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(graph_router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"message": "Welcome to PDF RAG API"}
