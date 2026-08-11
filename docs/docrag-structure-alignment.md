# docRAG structural alignment

Papercraft shares the proven application boundaries used by docRAG while
adding the scientific-paper enrichment and curation features that are
specific to this project. This note records the comparison so future work
preserves that shape intentionally rather than copying directories without a
reason.

## What stays aligned

| Responsibility | Shared location | Boundary |
| --- | --- | --- |
| HTTP transport | `app/api/` | Routes validate requests and translate application errors to HTTP responses. |
| Configuration and security | `app/core/` | Environment-derived settings and security helpers do not depend on transport code. |
| Background work | `app/worker/` | Celery tasks schedule and report work; the pipeline owns the processing sequence. |
| Ingestion orchestration | `app/pipeline/` | One pipeline coordinates OCR, parsing, indexing, and optional enrichment steps. |
| Domain processing | `app/paper/`, `app/citations/`, `app/graph/`, `app/embeddings/`, `app/llm/` | Each module exposes domain logic that can be tested without an HTTP request. |
| External integrations | `app/services/` | OCR, LLM, embedding-model, and metadata clients keep provider details out of routes and domain code. |
| Persistence | `app/storage/`, `app/db/` | Repository/client adapters isolate vector, graph, object, and relational stores. |

The existing `PaperIngestionPipeline` and `HybridRetriever` therefore remain
the composition points. A new provider, datastore, or extraction step should
be added behind one of those boundaries instead of reaching across layers.

## Intentional Papercraft extensions

Papercraft is not a directory-for-directory copy of docRAG. It extends the
same base with these project-specific capabilities:

- `app/services/openalex.py` enriches paper metadata before graph creation.
- `app/graph/llm_extractor.py` and `app/graph/confidence_router.py` add an
  opt-in LLM extraction path without making the deterministic path depend on
  an LLM.
- `app/services/curation.py`, `app/api/curation_routes.py`, and the
  relational models implement the human review queue and attestations.
- `app/storage/object_store.py` supports durable PDF storage beyond the
  local upload volume.

The vector-store adapter is deliberately allowed to differ: Papercraft uses
the Weaviate wrapper, whereas docRAG uses Qdrant. Callers should depend on
`VectorRepository`, not on either vendor's client API.

## Dependency and CI rule

The lightweight CI job excludes heavyweight OCR and embedding packages. Any
module imported by routes or tests in that job must therefore keep optional
imports inside the operation that needs them. In particular,
`app.services.embeddings.get_model()` imports `SentenceTransformer` lazily.
The service package exports the `embeddings` module so route-level patches
have a stable target, and CI installs NumPy explicitly for the test
environment.

This preserves the docRAG-style layered layout while keeping the fast test
path independent from Torch-sized optional dependencies.

## Decision

No directory reorganization is needed. Future changes should retain the
boundaries above, add dependencies behind adapters, and update this note when
a new cross-cutting subsystem changes the application shape.
