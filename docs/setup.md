# Developer Setup Guide (Phase 19.2)

This covers running the full GraphRAG stack (API, worker, Redis, Qdrant,
Neo4j, frontend) locally, plus the test suite and evaluation runner. For
what each piece does, see [`architecture.md`](./architecture.md).

## Prerequisites

- Docker & Docker Compose (recommended path -- runs everything below for you)
- Node.js 18+ (only needed if you're developing the frontend outside Docker)
- Python 3.11+ (only needed if you're running the backend outside Docker)

## 1. Environment variables

Copy the example file and fill in anything you need to change:

```bash
cp .env.example .env
```

| Variable | Default | Notes |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker + result backend |
| `QDRANT_URL` | `http://qdrant:6333` | Vector store |
| `QDRANT_API_KEY` | _(empty)_ | Only needed for Qdrant Cloud |
| `QDRANT_COLLECTION_NAME` | `documents` | Shared by the legacy chat path and GraphRAG |
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph store |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `password` | Must match `docker-compose.yml`'s `NEO4J_AUTH` |
| `LLM_PROVIDER` | `ollama` | `ollama` (local, free) or `openrouter` (cloud, paid) |
| `LLM_MODEL` | `llama3` | See README.md for provider-specific model names |
| `EMBEDDING_PROVIDER` | `local` | `local` (sentence-transformers) \| `openai` \| `stub` (deterministic, no model download -- tests/dev only) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Only used when `EMBEDDING_PROVIDER=local` |
| `OPENAI_API_KEY` | _(empty)_ | Required only if `EMBEDDING_PROVIDER=openai` |
| `MAX_UPLOAD_MB` | `50` | PDF upload size limit |

Neo4j and Qdrant connections are **optional at the pipeline level** --
if either is unreachable, ingestion still completes (graph or vector
steps are marked `SKIPPED`, not failed), but a live `/graph-query` request
returns `503` if it can't reach a service it actually needs for that
request.

## 2. Running with Docker Compose (recommended)

```bash
docker-compose up -d --build
```

This starts:

| Service | Port(s) | Purpose |
|---|---|---|
| `redis` | 6379 | Celery broker/backend |
| `qdrant` | 6333 | Vector store |
| `neo4j` | 7474 (browser UI), 7687 (Bolt) | Graph store |
| `ollama` | 11434 | Local LLM (only used if `LLM_PROVIDER=ollama`) |
| `api` | 8000 | FastAPI backend |
| `worker` | -- | Celery ingestion worker |
| `frontend` | 8080 | Angular app (built, served via nginx) |

The `api` and `worker` containers both wait for Neo4j's healthcheck
(`cypher-shell ... RETURN 1`) before starting, so a fresh `docker-compose
up` may take ~30s before the API is reachable.

Browse the graph directly at **http://localhost:7474** (login
`neo4j` / `password`, or whatever you set in `.env` / `docker-compose.yml`).

If using Ollama, pull the model once the containers are up:

```bash
docker-compose exec ollama ollama pull llama3
```

Run the test suite inside the container:

```bash
make test
# equivalent to: docker-compose run api pytest
```

## 3. Running the backend without Docker

Useful for fast iteration. Requires Redis, Qdrant, and Neo4j reachable
somehow (either run just those three via
`docker-compose up -d redis qdrant neo4j`, or point `.env` at existing
instances).

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
```

> **Note (Windows, Python 3.13):** the pinned `pymupdf`/`python-doctr`
> versions in `requirements.txt` don't ship prebuilt wheels for every
> Python version and may need Visual Studio Build Tools to compile from
> source. If `pip install -r requirements.txt` fails on those two
> packages specifically, install everything else first, then `pip install
> pymupdf sentence-transformers "python-doctr[torch]"` without pinning to
> pick up whatever version has a wheel for your interpreter -- the rest of
> the codebase only depends on their public APIs, not exact versions.

Start the API:

```bash
uvicorn app.api.main:app --reload --port 8000
```

Start the Celery worker (separate terminal):

```bash
celery -A app.worker.celery_app worker --loglevel=info
```

## 4. Running the frontend

```bash
cd frontend-angular
npm install
npm start
```

Serves at http://localhost:4200, pointed at `http://localhost:8000` by
default (see `src/environments/environment.ts`; the deployed build instead
defaults to the API URL baked into `ApiService.DEFAULT_API_URL`, and can
be overridden at runtime via the header's API URL field).

## 5. Running tests

Backend (from the repo root, with the venv above active):

```bash
pytest
```

All ~264 tests are unit tests with Neo4j/Qdrant/the LLM mocked --
none require the Docker services to be running.

Frontend:

```bash
cd frontend-angular
ng build      # compiles + type-checks everything
```

(No unit test suite exists for the frontend yet; `ng build` is the
fastest way to catch a broken component.)

## 6. Running the evaluation suite

Unlike the unit tests, `evaluation/run_eval.py` **does** need live Neo4j +
Qdrant with at least a few papers already ingested (upload some PDFs
through `/api/v1/upload` first -- see `scripts/load_small_pdf.sh` /
`scripts/load_large_pdf.sh`):

```bash
python -m evaluation.run_eval --top-k 5
```

Writes a JSON report to `evaluation/results.json` (per-mode summary +
full per-question detail comparing graph-only, vector-only, and hybrid
retrieval). Use `--limit N` for a quick smoke test against the first N
questions, and `--questions PATH` / `--output PATH` to point at different
files.

## 7. Common issues

See README.md's Troubleshooting section for the vector-RAG-specific
issues (Ollama model not found, CORS, embedding model changes, etc.).
GraphRAG-specific:

- **`/graph-query` returns 503** -- Neo4j or Qdrant isn't reachable from
  the API container/process. Check `docker-compose ps` and
  `docker-compose logs neo4j`.
- **`GET /papers/{paper_id}/graph` returns 404 for a paper you just
  uploaded** -- ingestion is async; check
  `GET /api/v1/status/{task_id}` first to confirm the `NEO4J_STORE` step
  succeeded (not `SKIPPED` -- that means Neo4j wasn't reachable during
  ingestion, in which case re-ingest with `?force=true` once it is).
- **Citation/entity graph looks sparse** -- entity and relation extraction
  are deterministic pattern matchers (see `architecture.md` section 6),
  not an LLM -- they only recognize the method/dataset/task/metric names
  and phrasings they're built to match.
