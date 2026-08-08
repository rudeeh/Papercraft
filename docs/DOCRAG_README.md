# docRAG v3 — GraphRAG for Research Papers

docRAG v3 builds on the vector-RAG PDF Q&A system from v1/v2 and adds a full **GraphRAG** layer on top: every ingested paper is parsed into a knowledge graph (methods, datasets, tasks, metrics, claims, experiments, citations) in Neo4j, in addition to the existing chunk embeddings in Qdrant. Questions get routed to graph traversal, vector search, or both, depending on what they're actually asking — "which papers cite the Transformer paper" is answered by walking `CITES` edges, not by hoping a similarity search happens to surface the right chunk.

The original vector-RAG path (`/chat`) is left completely intact and still works standalone; GraphRAG (`/graph-query`) is additive, not a replacement. See [`docs/architecture.md`](docs/architecture.md) and [`docs/decisions.md`](docs/decisions.md) for the full design writeup this README summarizes.

## Two Retrieval Paths, Side by Side

| | Legacy vector-RAG | GraphRAG |
|---|---|---|
| Endpoint | `POST /api/v1/chat` | `POST /api/v1/graph-query` |
| Storage | Qdrant only | Neo4j (graph) + Qdrant (graph-linked vectors) |
| Chunking | Fixed-size word chunks of raw OCR text | Per-section / per-entity chunks tagged with `node_type`/`node_name` |
| Answers | Stuffs top-K chunks into an LLM prompt | Routes to graph traversal and/or vector search based on query type, refuses to answer if nothing was retrieved |
| Citations | Page/snippet only | Structural: `CITES`/`CITED_BY` edges, citation-chain expansion, source paper list |
| Understands | "What does this document say about X" | + "which papers cite X", "what improved on Y", "how did Z evolve across papers" |

Both paths share the same PDF upload endpoint and the same Celery worker — ingestion runs the legacy chunk/embed path *and* the graph-extraction path in the same task, and a graph-extraction failure never blocks the vector-RAG path from completing (see [Ingestion Pipeline](#ingestion-pipeline) below).

## Architecture

### Ingestion pipeline

```
PDF upload (POST /api/v1/upload)
  └─ Celery task: app.worker.tasks.process_pdf_task
       └─ app.pipeline.paper_ingestion_pipeline.PaperIngestionPipeline.process()

           1. OCR          app/services/ocr.py            [CRITICAL]
              Native PyMuPDF text extraction first; doctr OCR only runs on
              pages with little/no extractable text.
           2. Parsing      app/paper/parser.py             [CRITICAL]
              Title, abstract, sections, references.
           3. Citations    app/citations/{extractor,normalizer}.py
              Raw reference + in-text mention extraction, then DOI/arXiv-
              based dedup and merge.
           4. Entities     app/graph/entity_extractor.py
              Method / Dataset / Task / Metric / Claim / Experiment, via
              deterministic term + regex matching (no heavy NLP dependency).
           5. Relations    app/graph/relation_extractor.py
              Co-occurrence + linguistic pattern matching, mapped to
              ontology edge types.
           6. Graph build  app/graph/paper_graph_builder.py
              Assembles Node/Edge objects, dedups, creates citation "stub"
              Paper nodes for not-yet-ingested cited papers.
           7. Neo4j store  app/storage/{neo4j_client,graph_repository}.py
              MERGE-based writes — re-ingesting a paper never duplicates.
           8. Vector chunks  built from parsed abstract + sections +
              entities (NOT raw OCR text) — each chunk carries
              node_type / node_name / source_text.
           9. Embedding    app/embeddings/embedder.py
              local (sentence-transformers) | openai | stub backend.
          10. Qdrant store  app/storage/{qdrant_client,vector_repository}.py
              Re-ingestion-safe: deletes the paper's old vectors first.
```

Steps 3–10 are all **non-critical**: each is wrapped in try/except by `PaperIngestionPipeline._run_step`, so a single failing step is recorded in `PipelineResult.steps` and the pipeline continues rather than aborting. Neo4j and Qdrant are both optional at the pipeline level — if a connection isn't available, the corresponding steps are marked `SKIPPED`, not `ERROR`.

### Query flow (GraphRAG)

```
POST /api/v1/graph-query {query, project_id?, top_k?, api_key?}
  app/api/graph_routes.py
    └─ HybridRetriever.retrieve(query, top_k)          app/retrieval/hybrid_retriever.py
         1. QueryClassifier.classify(query)              app/retrieval/query_classifier.py
              → EXPLANATION | COMPARISON | EVOLUTION | CITATION | SURVEY | ENTITY_LOOKUP
         2. Route per query type (table below):
              GraphRetriever      (Neo4j)      app/retrieval/graph_retriever.py
              VectorRetriever     (Qdrant)     app/retrieval/vector_retriever.py
              CitationExpander    (Neo4j, EVOLUTION only)  app/retrieval/citation_expander.py
    └─ ContextBuilder.build(retrieval_result)            app/llm/context_builder.py
         Renders graph facts / text evidence / citation paths / source
         papers into one prompt-ready text block.
    └─ AnswerGenerator.generate(query, retrieval_result)  app/llm/answer_generator.py
         Prompts app/services/llm.py with a grounding-only system prompt;
         refuses to answer (without calling the LLM) if retrieval found
         nothing, rather than letting the model guess.
  ← {answer, sources, retrieval_trace}
```

**Query routing table** (`HybridRetriever`):

| Query type | Example | Retrieval used |
|---|---|---|
| `EXPLANATION` | "Explain how self-attention works" | Vector only |
| `CITATION` | "Which papers cite the Transformer paper?" | Graph only |
| `EVOLUTION` | "How did attention mechanisms evolve across papers?" | Citation expansion + Graph |
| `COMPARISON` | "How does BERT compare to GPT?" | Graph + Vector |
| `SURVEY` | "What methods have been used for X?" | Graph + Vector |
| `ENTITY_LOOKUP` | "What is the Transformer?" | Graph only |

Graph queries are anchored two ways: entities spotted in the query text itself (reusing `EntityExtractor`'s deterministic matching against the question instead of paper text), and an optional explicit `paper_id` when the caller already knows which paper is in scope. `HybridRetriever.retrieve()` also accepts a `force_mode` (`"graph"` | `"vector"` | `"both"`) that bypasses the routing table entirely — this is what the [evaluation harness](#evaluation-harness) uses to compare all three modes on the same question.

### Storage design

**Neo4j (graph)** — ontology defined in `app/graph/ontology.py`:

- **Node types:** `Paper`, `Method`, `Dataset`, `Task`, `Metric`, `Author`, `Institution`, `Claim`, `Experiment`, `Section`
- **Edge types:** `CITES`, `CITED_BY`, `INTRODUCES`, `USES_METHOD`, `IMPROVES_UPON`, `EXTENDS`, `VARIANT_OF`, `USES_DATASET`, `PUBLISHED_DATASET`, `EVALUATES_ON`, `BENCHMARK_FOR`, `SOLVES_TASK`, `RELATED_TASK`, `REPORTS_METRIC`, `MEASURED_BY`, `WRITTEN_BY`, `AUTHORED_BY`, `AFFILIATED_WITH`, `HAS_SECTION`, `CONTAINS_CLAIM`, `MENTIONS`, `COMPARES_TO`
- A `VALID_EDGES` map restricts `(source_type, edge_type) → {allowed target types}`; `OntologyValidator` rejects anything outside it at construction time, so every node/edge that makes it into Neo4j is ontology-conformant by construction. Relations that don't fit are silently dropped rather than stored loosely (`PaperGraphBuilder._safe_edge` catches the `ValueError`).
- All writes go through `Neo4jClient.merge_node` / `merge_edge` (Cypher `MERGE`, not `CREATE`), keyed by a fixed `NODE_KEY_MAP` — re-ingesting a paper never creates duplicates.
- Citations to not-yet-ingested papers become **stub Paper nodes** (`is_stub: true`, ID derived from DOI → arXiv ID → title hash, in that order) so cross-paper citation edges exist immediately. When the real paper is later ingested, `GraphRepository.resolve_citation_stub` re-wires incoming `CITES` edges to it and deletes the stub.

**Qdrant (vectors)** — single collection (default name `documents`), managed by `QdrantClientWrapper` + `VectorRepository`. Each point's payload:

```json
{
  "paper_id": "...",
  "text": "...",
  "section": "Abstract | <section heading>",
  "node_type": "Paper | Section | Method | Dataset | Task | Metric | Claim | Experiment",
  "node_name": "...",
  "source_text": "...",
  "page": 1,
  "chunk_index": 0
}
```

`node_type`/`node_name` link every vector back to the graph node it was derived from — this is what lets `VectorRetriever` filter by node type and lets citation expansion restrict vector search to a specific set of `paper_id`s. Re-ingesting a paper deletes its existing points first, so re-ingestion is idempotent the same way Neo4j writes are.

> The legacy `/chat` path writes into the *same* Qdrant collection with a different payload shape (`doc_id`/`filename` instead of `paper_id`/`node_type`/`node_name`). The two coexist safely because neither path filters on the other's fields — see [`docs/decisions.md`](docs/decisions.md) for why this wasn't consolidated into one embedding path.

### Component map

| Phase | Concern | Key files |
|---|---|---|
| 2 | Ontology | `app/graph/ontology.py` |
| 3 | Paper parsing | `app/paper/parser.py` |
| 4 | Citation extraction | `app/citations/{extractor,normalizer}.py` |
| 5 | Entity extraction | `app/graph/entity_extractor.py` |
| 6 | Relation extraction | `app/graph/relation_extractor.py` |
| 7 | Paper graph builder | `app/graph/paper_graph_builder.py` |
| 8 | Neo4j storage | `app/storage/{neo4j_client,graph_repository}.py` |
| 9 | Vector indexing | `app/embeddings/embedder.py`, `app/storage/{qdrant_client,vector_repository}.py` |
| 10 | Ingestion pipeline | `app/pipeline/paper_ingestion_pipeline.py`, `app/worker/tasks.py` |
| 11 | Graph retrieval | `app/retrieval/graph_retriever.py` |
| 12 | Vector retrieval | `app/retrieval/vector_retriever.py` |
| 13 | Hybrid retrieval router | `app/retrieval/{query_classifier,hybrid_retriever}.py` |
| 14 | Citation expansion | `app/retrieval/citation_expander.py` |
| 15 | Answer generation | `app/llm/{context_builder,answer_generator}.py` |
| 16 | API integration | `app/api/graph_routes.py` |
| 17 | Frontend integration | `frontend-angular/src/app/components/{ask,citation-explorer}/` |
| 18 | Evaluation | `evaluation/{questions.json,run_eval.py}` |

### Design principle

Entity/relation extraction, query classification, and citation mention-parsing are all **deterministic regex/keyword heuristics** — not an LLM, not a spaCy/transformers NER model. This keeps ingestion fast, fully testable (every extractor has a millisecond-fast unit test suite, no API key or GPU required), and keeps the *only* place an LLM is actually called down to answer generation, where grounding it against retrieved context matters far more than at extraction time. The tradeoff: recall is bounded by the known-term vocabulary and pattern coverage — a method or relation phrased in an unanticipated way won't be extracted. See [`docs/decisions.md`](docs/decisions.md) for the full reasoning (Neo4j vs. alternatives, why Qdrant wasn't replaced, why the ontology is closed, etc.).

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| API | FastAPI | REST endpoints, request validation, rate limiting |
| Task queue | Celery + Redis | Async PDF ingestion |
| OCR | PyMuPDF (native) + doctr (fallback) | Text extraction; doctr only runs on pages PyMuPDF can't read |
| Vector store | Qdrant | Chunk embeddings, top-K similarity search |
| Graph store | Neo4j 5 (+ APOC) | Knowledge graph: papers, methods, datasets, tasks, metrics, claims, citations |
| Embeddings | Sentence-Transformers (default), OpenAI, or stub | Local-first, pluggable backend |
| LLM | OpenRouter (sole provider — see [`docs/decisions.md`](docs/decisions.md)) | Answer generation, grounded against retrieved context |
| Frontend | Angular 17 | Standalone components + signals |
| Rate limiting | slowapi | Per-IP limits on `/upload` and `/chat` |
| Logging | structlog | Structured JSON logs |

## Project Structure

```
docRAG_v3/
├── app/
│   ├── api/
│   │   ├── main.py                 # FastAPI app, CORS, rate limiter, pre-warms embedding model on startup
│   │   ├── routes.py               # Legacy: /chat, /upload, /status, /health, /llm-status
│   │   └── graph_routes.py         # GraphRAG: /graph-query, /citation-graph
│   ├── core/config.py              # Pydantic Settings, reads .env
│   ├── services/                   # Legacy vector-RAG: ocr, text_processing, embeddings, vector_store, llm
│   ├── paper/parser.py             # Title/abstract/sections/references extraction
│   ├── citations/                  # extractor.py, normalizer.py
│   ├── graph/                      # ontology.py, entity_extractor.py, relation_extractor.py, paper_graph_builder.py
│   ├── embeddings/embedder.py      # GraphRAG embedding backend (local/openai/stub)
│   ├── storage/                    # neo4j_client, graph_repository, qdrant_client, vector_repository
│   ├── retrieval/                  # query_classifier, graph_retriever, vector_retriever, citation_expander, hybrid_retriever
│   ├── llm/                        # context_builder.py, answer_generator.py
│   ├── pipeline/paper_ingestion_pipeline.py   # Orchestrates all 10 ingestion steps
│   └── worker/                     # celery_app.py, tasks.py (process_pdf_task)
├── frontend-angular/
│   └── src/app/components/
│       ├── upload/                 # Drag & drop PDF upload
│       ├── status/                 # Task status polling
│       ├── ask/                    # Smart Q&A (/graph-query) + Quick Search (/chat) toggle
│       ├── citation-explorer/      # Global citation graph (every ingested paper + CITES edges)
│       ├── recent-tasks/
│       └── header/
├── evaluation/
│   ├── questions.json              # Labeled eval set: category, expected entities/relations/sources
│   └── run_eval.py                 # Scores graph/vector/hybrid retrieval against questions.json
├── docs/
│   ├── architecture.md             # Full system design (source for this README's Architecture section)
│   ├── decisions.md                # ADR-style log: why Neo4j, why keep Qdrant, why deterministic extraction, ...
│   ├── repo_audit.md               # Module-by-module audit of the pre-GraphRAG codebase
│   └── setup.md
├── tests/                          # 25 test files — unit + integration, see Testing below
├── scripts/                        # load_small_pdf.sh, load_large_pdf.sh, query_rag.sh, health_check.sh, create_test_pdf.py
├── docker-compose.yml               # redis, qdrant, neo4j, api, worker, frontend
├── Dockerfile
├── Makefile
├── pytest.ini
└── requirements.txt
```

## Requirements

- Docker & Docker Compose (backend + Neo4j + Qdrant + Redis)
- Node.js 18+ (frontend development)
- An [OpenRouter](https://openrouter.ai) API key (or plan to have each user supply their own — see below)
- (Optional) `make` for the `Makefile` shortcuts

## Setup

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

### LLM Configuration

All LLM inference goes through **OpenRouter** — there's no local Ollama option in v3 (removed to cut down on services to run; see [`docs/decisions.md`](docs/decisions.md)).

```env
OPENROUTER_API_KEY=sk-or-v1-your-key-here
LLM_MODEL=openai/gpt-oss-20b:free
```

Get a key at https://openrouter.ai/keys. Model IDs on OpenRouter change over time — if you get a `404 No endpoints found for <model>`, the `LLM_MODEL` value has been retired; check https://openrouter.ai/models (filter by "Free" for no-cost options) rather than trusting any hardcoded list.

**`OPENROUTER_API_KEY` is optional on the server.** If left unset, `/chat` and `/graph-query` requests must supply their own key (the frontend's header has an "OpenRouter Key" field for this, stored in `localStorage`, sent per-request, never written server-side). This is meant for public/shared deployments where you don't want to pay for everyone's usage. `GET /api/v1/llm-status` reports whether a server key is configured, which the frontend uses to decide whether to show that field as required.

### Embedding Configuration

```env
EMBEDDING_PROVIDER=local     # "local" (sentence-transformers) | "openai" | "stub" (tests/dev only)
EMBEDDING_MODEL=all-MiniLM-L6-v2
EMBEDDING_BATCH_SIZE=64
# OPENAI_API_KEY=sk-xxxxx    # required only if EMBEDDING_PROVIDER=openai
```

As in v1/v2: if you change `EMBEDDING_MODEL` (or `EMBEDDING_PROVIDER`) after ingesting papers, re-ingest them — the old vectors are incompatible.

### Full `.env` reference

```env
# ─── Redis ───
REDIS_URL=redis://redis:6379/0

# ─── Qdrant ───
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=documents
QDRANT_DISTANCE_METRIC=cosine
QDRANT_BATCH_SIZE=128

# ─── Neo4j ───
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j

# ─── LLM (OpenRouter only) ───
OPENROUTER_API_KEY=sk-or-v1-your-key-here
LLM_MODEL=openai/gpt-oss-20b:free

# ─── Embeddings ───
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=all-MiniLM-L6-v2
EMBEDDING_BATCH_SIZE=64
# OPENAI_API_KEY=sk-xxxxx

# ─── RAG ───
RAG_TOP_K=5
MAX_CONTEXT_TOKENS=4096
CHUNK_TOKENS=500
CHUNK_OVERLAP_TOKENS=50

# ─── Celery ───
CELERY_CONCURRENCY=2

# ─── Upload ───
MAX_UPLOAD_MB=50
UPLOAD_DIR=/app/uploads
```

## Running

### Backend (Docker)

```bash
docker-compose up -d --build
```

This launches:

| Service | Port(s) | Notes |
|---|---|---|
| Redis | 6379 | Celery broker/backend |
| Qdrant | 6333 | Vector database |
| Neo4j | 7474 (browser UI), 7687 (Bolt) | Graph database; API/worker wait on its healthcheck before starting |
| API | 8000 | FastAPI backend |
| Worker | — | Celery background processing |
| Frontend | 8080 | Only if you run the `frontend` compose service instead of `npm start` |

Open the Neo4j Browser at http://localhost:7474 (user `neo4j`, password `password` by default) to inspect the graph directly with Cypher — useful for sanity-checking what actually got extracted from a paper.

Or with the Makefile: `make up` / `make down` / `make build` / `make logs`.

### Frontend (Angular)

**Local development:**
```bash
cd frontend-angular
npm install
npm start
```
Runs at http://localhost:4200, talks to the backend at http://localhost:8000.

**Production build:**
```bash
cd frontend-angular
npm run build
```
Output: `frontend-angular/dist/docrag-frontend/browser/`.

**Docker (optional):**
```bash
cd frontend-angular
docker build -t docrag-frontend .
docker run -p 8080:80 docrag-frontend
```

### Frontend components

- **upload** — drag & drop PDF upload, with optional force re-processing.
- **status** — polls `/status/{task_id}` for ingestion progress.
- **ask** — a single question box with a **Smart Q&A** (`/graph-query`) / **Quick Search** (`/chat`) toggle. Smart Q&A understands citations and cross-paper relationships and is the recommended default; Quick Search is a plain vector-only fallback, useful if the graph store is unavailable or to scope a search to one document. Replaces two separate near-identical query cards from earlier in development.
- **citation-explorer** — the global citation graph: every ingested paper (real or stub) and every `CITES` edge, from `GET /citation-graph`. Deliberately simple (static circle layout, no force simulation/zoom/pan) — click a paper to inspect it.

## Deployment

### Backend

Same pattern as v1/v2 (Render or any Docker host) with one addition: **Neo4j needs to be reachable too** — either run it as a container alongside the API (as `docker-compose.yml` does) or point `NEO4J_URI` at a hosted instance (e.g. [Neo4j Aura](https://neo4j.com/cloud/platform/aura-graph-database/)). CORS is open to all origins (`allow_origins=["*"]`).

### Frontend

Same as v1/v2 — deploy `frontend-angular/` to Vercel (or similar) with build command `npm run build` and output directory `dist/docrag-frontend/browser`. Update the backend URL in `src/app/services/api.service.ts` (`DEFAULT_API_URL`) and `src/index.html` if it differs from the default.

## Usage

### Quick Start Workflow

1. **Upload a paper** — drag & drop a PDF, note the returned `doc_id`/`task_id`.
2. **Wait for ingestion** — status moves PENDING → STARTED → SUCCESS. The graph-extraction steps run in the background; a failure in any one of them doesn't fail the whole task (check `PipelineResult.steps` in the task result for a per-step breakdown).
3. **Ask** — use Smart Q&A for anything involving citations, comparisons, or "how did X evolve" questions; use Quick Search for plain in-document lookup.
4. **Explore** — see the citation graph across everything you've ingested.

### Command Line

```bash
./scripts/load_small_pdf.sh "/path/to/document.pdf"
./scripts/load_large_pdf.sh "/path/to/large-document.pdf"
./scripts/query_rag.sh "What is the main topic of the document?"
./scripts/health_check.sh   # runs the test suite, boots the API, builds the frontend — see Development below
```

## API Reference

All endpoints are prefixed with `/api/v1`.

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/health` | GET | Health check |
| `/api/v1/llm-status` | GET | Whether the server has a default OpenRouter key configured |
| `/api/v1/upload` | POST | Upload a PDF for processing (rate-limited: 5/min) |
| `/api/v1/status/{task_id}` | GET | Check ingestion status |
| `/api/v1/chat` | POST | Legacy vector-only Q&A (rate-limited: 20/min) |
| `/api/v1/graph-query` | POST | GraphRAG Q&A — hybrid graph + vector retrieval |
| `/api/v1/citation-graph` | GET | The full cross-paper citation network (all papers/stubs + `CITES` edges) |

### Example: GraphRAG query

```bash
curl -X POST "http://localhost:8000/api/v1/graph-query" \
  -H "Content-Type: application/json" \
  -d '{"query": "Which papers cite the Transformer paper?", "top_k": 10}'
```

```json
{
  "answer": "Two ingested papers cite \"Attention Is All You Need\": ...",
  "sources": [
    { "paper_id": "paper_arxiv_1706.03762", "title": "Attention Is All You Need" }
  ],
  "retrieval_trace": {
    "query_type": "CITATION",
    "graph_facts": [ { "subject": {"...": "..."}, "relation": "CITES", "object": {"...": "..."} } ],
    "vector_results": [],
    "citation_paths": [],
    "source_paper_ids": ["paper_arxiv_1706.03762"],
    "confidence_notes": "..."
  }
}
```

If retrieval finds nothing, `AnswerGenerator` returns a refusal *without ever calling the LLM* — it doesn't let the model guess at an answer with no grounding.

Pass `api_key` in the request body to use a caller-supplied OpenRouter key instead of (or in the absence of) a server-configured one — same mechanism as `/chat`.

### Example: single paper's graph

```bash
curl "http://localhost:8000/api/v1/papers/paper_arxiv_1706.03762/graph"
```
```json
{
  "paper_id": "paper_arxiv_1706.03762",
  "nodes": [ { "id": "...", "type": "Method", "name": "Self-Attention" } ],
  "edges": [ { "source": "...", "type": "INTRODUCES", "target": "..." } ]
}
```

### Legacy endpoints (`/chat`, `/upload`, `/status`)

Unchanged from v1/v2 — see the [v1 README](../docRAG/README.md#example-api-calls) for request/response shapes if you're only using the vector-RAG path.

## Evaluation Harness

`evaluation/questions.json` is a labeled set of questions, each tagged with a query category (`EXPLANATION`, `CITATION`, etc.) and, where checkable, `expected_entities` / `expected_relation` / `expected_sources`.

```bash
python -m evaluation.run_eval [--questions PATH] [--output PATH] [--top-k N] [--limit N]
```

`run_eval.py` runs every question through **graph-only, vector-only, and hybrid** retrieval (via `HybridRetriever`'s `force_mode`), scores each mode against the expected entities/relations/sources, and writes a JSON report comparing the three. Requires a running Neo4j + Qdrant with at least some papers already ingested — this evaluates *retrieval quality against the ontology*, not the correctness of the ingested content itself.

## Testing

25 test files under `tests/`, covering both the legacy path and every GraphRAG phase:

| Area | Files |
|---|---|
| Graph construction | `test_ontology.py`, `test_entity_extractor.py`, `test_relation_extractor.py`, `test_paper_graph_builder.py` |
| Parsing & citations | `test_parser.py`, `test_citation_extraction.py` |
| Storage | `test_neo4j_client.py`, `test_graph_repository.py`, `test_qdrant_client.py`, `test_vector_repository.py` |
| Embeddings | `test_embedder.py` |
| Retrieval | `test_graph_retriever.py`, `test_vector_retriever.py`, `test_hybrid_retriever.py`, `test_query_classifier.py`, `test_citation_expander.py` |
| Answer generation | `test_llm.py`, `test_context_builder.py`, `test_answer_generator.py` |
| Pipeline & API | `test_paper_ingestion_pipeline.py`, `test_api.py`, `test_graph_query_api.py` |
| Integration | `test_integration_phase_1_3.py`, `test_integration_phase_4_5.py` |
| Evaluation | `test_run_eval.py` |

```bash
docker-compose run api pytest          # everything
pytest tests/test_hybrid_retriever.py  # a single module (from a local venv)
```

`scripts/health_check.sh` runs a broader local sanity pass: the core test subset, a real API boot + `/health` poll, and a frontend production build — useful as a single pre-deploy check.

## Development

- **`docs/architecture.md`** — the authoritative system design doc; this README's Architecture section is a condensed version of it.
- **`docs/decisions.md`** — ADR-style log explaining *why* (Neo4j vs. alternatives, why Qdrant wasn't replaced, why the graph pipeline sits beside the legacy path instead of replacing it, why the ontology is closed, why extraction is deterministic instead of LLM-based).
- **`docs/repo_audit.md`** — module-by-module audit of the codebase as it stood before GraphRAG work started, for context on what changed.

```bash
docker-compose logs -f api      # API logs
docker-compose logs -f worker   # worker logs (ingestion pipeline output)
docker-compose logs -f neo4j    # graph store logs
```

## Troubleshooting

**"No OpenRouter API key configured" / 401 from `/chat` or `/graph-query`** — The server has no `OPENROUTER_API_KEY` and the request didn't supply one either. Set it in `.env` and `docker-compose restart api`, or enter a key in the frontend's "OpenRouter Key" field.

**Model not found / invalid model error from OpenRouter** — Confirm `LLM_MODEL` is a valid OpenRouter model *slug* (e.g. `meta-llama/llama-3-8b-instruct:free`), not a bare model name — see https://openrouter.ai/models.

**Graph queries return no facts even though papers were ingested** — Check the worker logs for the ingestion task's per-step results; a paper can end up with vectors but no graph data (or vice versa) if Neo4j or Qdrant was unreachable during ingestion, since the pipeline treats each store as independently optional. Re-upload with `force=true` once both stores are reachable.

**Neo4j fails its healthcheck / API and worker won't start** — `docker-compose logs neo4j`; the API/worker containers wait on Neo4j's `cypher-shell` healthcheck before starting, so a slow first boot (schema/plugin init) is expected — give it the `start_period` (30s) before assuming it's actually stuck.

**API shows "Offline" in the frontend** — Check `docker-compose ps`, check `docker-compose logs api`, verify the API URL in the frontend header, and (if deployed) make sure the backend isn't asleep.

**CORS errors in the browser console** — CORS is enabled for all origins already; check for a trailing slash mismatch, or that the backend actually responded.

**OCR processing fails** — Check `docker-compose logs -f worker`. Remember most text extraction is native PyMuPDF; doctr only kicks in for pages with no extractable text, so this usually means a genuinely low-quality scan.

**Status stays "PENDING"** — Confirm the worker is running (`docker-compose ps`), check its logs, confirm Redis/Qdrant/Neo4j are all healthy.

**No results when querying** — Confirm ingestion completed (status: SUCCESS), check the worker logs for which pipeline steps succeeded, try a more specific question, or try `/chat` (vector-only) to isolate whether the issue is graph-specific.

**Changed embedding model but search doesn't work** — Old vectors are incompatible with a new `EMBEDDING_MODEL`/`EMBEDDING_PROVIDER`; re-ingest documents to regenerate them.

**Frontend build fails**
```bash
cd frontend-angular
rm -rf node_modules package-lock.json
npm install
npm run build
```

## Known Limitations

(See [`docs/decisions.md`](docs/decisions.md) for the full cost/tradeoff discussion behind each of these.)

- **Extraction recall is bounded by known-term vocabulary and regex patterns** — a method, dataset, or relation phrased in a way `EntityExtractor`/`RelationExtractor` don't anticipate simply won't be extracted. There's no LLM-based fallback by design.
- **No cross-store transaction between Neo4j and Qdrant** — each is independently re-ingestion-safe, but a paper can in principle end up with a graph and no vectors (or vice versa) if one store is down mid-ingestion. The pipeline's per-step status tracking makes this visible rather than silent.
- **Two embedding/chunking code paths write into the same Qdrant collection** — the legacy `/chat` path and the GraphRAG path use different payload shapes and different chunk granularities. They don't conflict (neither filters on the other's fields) but haven't been consolidated.
- **Real relationships outside the fixed ontology are dropped, not stored loosely** — extending the graph to a new kind of fact requires updating `app/graph/ontology.py` first.
