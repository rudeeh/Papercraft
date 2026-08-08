# Developer Setup Guide

This covers running the full Papercraft stack (API, worker, Redis, Weaviate,
Neo4j, PostgreSQL, MinIO, frontends) locally, plus the test suite and evaluation runner. For
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
| `WEAVIATE_URL` | `http://weaviate:8080` | Vector store (in-network; host port is 8082) |
| `WEAVIATE_GRPC_PORT` | `50051` | The v4 client needs gRPC as well as HTTP |
| `WEAVIATE_API_KEY` | _(empty)_ | Only needed for Weaviate Cloud |
| `WEAVIATE_COLLECTION_NAME` | `Documents` | Shared by the legacy chat path and GraphRAG |
| `POSTGRES_URI` | `postgresql+psycopg://papercraft:papercraft@postgres:5432/papercraft` | Users, jobs, drafts, attestations, audit log |
| `SECRET_KEY` | `dev-secret-change-me` | **Change before exposing the API** -- it signs every access token |
| `AUTH_REQUIRED` | `false` | `false` = single-player mode (anonymous curation allowed); `true` = bearer token required |
| `OBJECT_STORE_ENABLED` | `false` | `true` stores PDFs in MinIO/S3 instead of only `UPLOAD_DIR` |
| `OPENALEX_ENABLED` | `true` | OpenAlex metadata enrichment during ingestion |
| `OPENALEX_MAILTO` | _(empty)_ | Supplying an address joins OpenAlex's polite pool (better rate limits) |
| `EXTRACTION_PROVIDER` | `heuristic` | `heuristic` (no LLM cost) \| `llm` \| `hybrid` (both passes, agreement boosts confidence) |
| `CONFIDENCE_AUTO_INSERT` | `0.85` | At or above this an extraction goes straight into the graph |
| `CONFIDENCE_DRAFT` | `0.50` | At or above this it queues for review; below, it is flagged manual |
| `ATTESTATION_PROMOTE_SCORE` | `2` | Net upvotes needed to promote a draft |
| `NEO4J_URI` | `bolt://neo4j:7687` | Graph store |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `password` | Must match `docker-compose.yml`'s `NEO4J_AUTH` |
| `OPENROUTER_API_KEY` | _(empty)_ | Optional -- if unset, every `/chat`/`/graph-query` request must supply its own key (e.g. the frontend header's "OpenRouter Key" field) or it 401s |
| `LLM_MODEL` | `openai/gpt-oss-20b:free` | Must be a valid, currently-listed OpenRouter model slug -- these get renamed/retired over time, see https://openrouter.ai/models |
| `EMBEDDING_PROVIDER` | `local` | `local` (sentence-transformers) \| `openai` \| `stub` (deterministic, no model download -- tests/dev only) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Only used when `EMBEDDING_PROVIDER=local` |
| `OPENAI_API_KEY` | _(empty)_ | Required only if `EMBEDDING_PROVIDER=openai` |
| `MAX_UPLOAD_MB` | `50` | PDF upload size limit |

Neo4j, Weaviate, PostgreSQL and MinIO are all **optional at the pipeline
level** -- if any is unreachable, ingestion still completes (the affected
steps are marked `SKIPPED`, not failed). A live `/graph-query` request
still returns `503` if it can't reach a service it needs for *that*
request, and the auth/curation routes `503` without PostgreSQL.

One consequence worth knowing: with no PostgreSQL there is no draft queue,
so the pipeline writes review-needed extractions straight into the graph
rather than discarding them (see the confidence-routing ADR in
[`decisions.md`](./decisions.md)).

## 2. Running with Docker Compose (recommended)

```bash
docker-compose up -d --build
```

This starts:

| Service | Port(s) | Purpose |
|---|---|---|
| `redis` | 6379 | Celery broker/backend |
| `weaviate` | 8082 (HTTP), 50051 (gRPC) | Vector store |
| `postgres` | 5432 | Users, jobs, drafts, attestations, audit log |
| `minio` | 9000 (S3 API), 9001 (console) | Object storage for PDFs |
| `neo4j` | 7474 (browser UI), 7687 (Bolt) | Graph store |
| `api` | 8000 | FastAPI backend |
| `worker` | -- | Celery ingestion worker |
| `frontend` | 8080 | Angular app (built, served via nginx) |
| `web` | 3000 | Next.js app (ingest / ask / review queue) |

The `api` and `worker` containers both wait for Neo4j's healthcheck
(`cypher-shell ... RETURN 1`) before starting, so a fresh `docker-compose
up` may take ~30s before the API is reachable.

Browse the graph directly at **http://localhost:7474** (login
`neo4j` / `password`, or whatever you set in `.env` / `docker-compose.yml`).

All LLM calls go through OpenRouter -- set `OPENROUTER_API_KEY` in `.env`,
or leave it unset and supply a key per-request (see section 1 above).

Run the test suite inside the container:

```bash
make test
# equivalent to: docker-compose run api pytest
```

## 3. Running the backend without Docker

Useful for fast iteration. Requires Redis, Weaviate, Neo4j and PostgreSQL
reachable somehow (either run just those via
`docker compose up -d redis weaviate neo4j postgres`, or point `.env` at
existing instances).

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

All 577 tests are unit tests with Neo4j / Weaviate / OpenAlex / the LLM
mocked, and PostgreSQL substituted by in-memory SQLite -- none require the
Docker services to be running.

Frontend:

```bash
cd frontend-angular
ng build      # compiles + type-checks everything
```

(No unit test suite exists for the frontend yet; `ng build` is the
fastest way to catch a broken component.)

## 6. Running the evaluation suite

Unlike the unit tests, `evaluation/run_eval.py` **does** need live Neo4j +
Weaviate with at least a few papers already ingested (upload some PDFs
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
issues (OpenRouter model/key errors, CORS, embedding model changes, etc.).
GraphRAG-specific:

- **`/graph-query` or `/chat` returns 401** -- no OpenRouter API key
  available (neither `OPENROUTER_API_KEY` on the server nor one supplied
  with the request). Set one in `.env`, or in the frontend header's
  "OpenRouter Key" field.
- **`/graph-query` returns 503** -- Neo4j or Weaviate isn't reachable from
  the API container/process. Check `docker compose ps` and
  `docker compose logs neo4j`.
- **`/auth/*` or `/curation/*` returns 503** -- PostgreSQL isn't
  reachable. `docker compose logs postgres`; the rest of the API keeps
  working without it.
- **A paper doesn't show up in the citation graph after uploading** --
  ingestion is async; check `GET /api/v1/status/{task_id}` first to
  confirm the `NEO4J_STORE` step succeeded (not `SKIPPED` -- that means
  Neo4j wasn't reachable during ingestion, in which case re-ingest with
  `?force=true` once it is).
- **Citation/entity graph looks sparse** -- with the default
  `EXTRACTION_PROVIDER=heuristic`, entity and relation extraction are
  deterministic pattern matchers (see `architecture.md` section 6), so
  they only recognize the method/dataset/task/metric names and phrasings
  they are built to match. Set `EXTRACTION_PROVIDER=hybrid` (and an
  OpenRouter key) to add the LLM pass.
- **The review queue is always empty** -- expected on the default
  `heuristic` provider: deterministic extractions score above the
  auto-insert threshold, so nothing needs review. Switch to `hybrid`.
