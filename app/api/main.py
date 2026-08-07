from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app.api.graph_routes import router as graph_router
from app.core.config import settings
import structlog
import os
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

# Serve the citation constellation visualizer
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
STATIC_DIR = os.path.normpath(STATIC_DIR)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/graph-visualizer")
async def graph_visualizer():
    """Serve the interactive citation constellation visualization."""
    html_path = os.path.join(STATIC_DIR, "graph_visualizer.html")
    if os.path.exists(html_path):
        return FileResponse(html_path, media_type="text/html")
    return {"error": "graph_visualizer.html not found"}

@app.get("/")
async def root():
    return {"message": "Welcome to PDF RAG API"}
