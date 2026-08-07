# Repository Audit: docRAG_v3

**Date:** June 2026  
**Status:** Initial Setup for GraphRAG Enhancement

---

## Executive Summary

The `docRAG_v3` repository is a **document question-answering system** built on FastAPI, Celery, Redis, and Qdrant. The current architecture is a traditional **vector-based RAG pipeline**. This audit identifies:

- ✅ **Reusable components** that can be retained
- ⚠️ **Components requiring modification** for graph integration
- 🆕 **New modules to add** for GraphRAG functionality

---

## Current Architecture

### Overall Data Flow

```
PDF Upload
    ↓
FastAPI /api/v1/upload (rate-limited: 5/min)
    ↓
Celery Task Queue (Redis)
    ↓
Worker: process_pdf_task
    ├→ Step 1: OCR Extraction (doctr + PyMuPDF)
    ├→ Step 2: Chunking (text_processing.chunk_text)
    ├→ Step 3: Embeddings (sentence-transformers)
    └→ Step 4: Qdrant Upsert
    ↓
User Query
    ↓
FastAPI /api/v1/chat (rate-limited: 20/min)
    ├→ Embed query
    ├→ Vector search (Qdrant)
    ├→ Context building
    └→ LLM response (Ollama or OpenRouter)
```

### Deployment Architecture

- **Docker Compose** orchestration
- **Services:**
  - `redis` (task queue, port 6379)
  - `qdrant` (vector DB, port 6333)
  - `ollama` (local LLM, port 11434) — optional
  - `api` (FastAPI, port 8000)
  - `worker` (Celery, async)
  - `frontend` (Angular 17, port 8080)

---

## Detailed Module Analysis

### 1. FastAPI Backend (`app/api/`)

**Status:** ✅ **KEEP + EXTEND**

**Files:**
- `app/api/main.py` — FastAPI app setup, CORS, rate limiting, startup
- `app/api/routes.py` — API endpoints

**Current Endpoints:**
- `POST /api/v1/upload` — PDF upload with streaming hash verification
- `GET /api/v1/status/{task_id}` — Task status polling
- `POST /api/v1/chat` — Query with optional doc_id filtering
- `GET /api/v1/health` — Health check

**Strengths:**
- Clean route structure
- Async-ready
- Rate limiting in place (slow api)
- CORS enabled
- Structured logging (structlog)

**Modifications for GraphRAG:**
- Add `POST /api/v1/graph-query` — Graph-based queries (Phase 16)
- Add `GET /api/v1/papers/{paper_id}/graph` — Retrieve paper graph (Phase 16)
- Optionally add `/api/v1/citations/{paper_id}` — Citation metadata

**Reusable:**
- Route structure pattern
- Rate limiting setup
- Error handling pattern

---

### 2. Configuration (`app/core/`)

**Status:** ✅ **KEEP + EXTEND**

**Files:**
- `app/core/config.py` — Pydantic settings

**Current Config:**
```python
REDIS_URL
QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION_NAME
LLM_PROVIDER (ollama/openrouter), LLM_MODEL
EMBEDDING_MODEL (default: all-MiniLM-L6-v2)
RAG_TOP_K, CHUNK_TOKENS, CHUNK_OVERLAP_TOKENS
UPLOAD_DIR, MAX_UPLOAD_MB
```

**Modifications for GraphRAG:**
- Add `NEO4J_URI` (connection string)
- Add `NEO4J_USER`, `NEO4J_PASSWORD`
- Add `GRAPH_BATCH_SIZE` (entity/relation extraction batch size)
- Add `GRAPH_RETRIEVAL_DEPTH` (citation expansion depth limit)

---

### 3. Celery Worker (`app/worker/`)

**Status:** ✅ **KEEP + EXTEND**

**Files:**
- `app/worker/celery_app.py` — Celery app initialization
- `app/worker/tasks.py` — `process_pdf_task` (main ingestion task)

**Current Task:**
```python
process_pdf_task(doc_id, file_path):
  1. OCR extraction
  2. Chunking
  3. Embeddings
  4. Qdrant upsert
```

**Modifications for GraphRAG:**
- Extend `process_pdf_task` to call **graph pipeline** after OCR step:
  - After text extraction: extract entities, relations, citations
  - Before Qdrant upsert: build paper graph
  - After Qdrant: store graph in Neo4j
- Keep existing Qdrant path for backward compatibility

**Example Extended Flow:**
```python
process_pdf_task(doc_id, file_path):
  1. OCR extraction
  2. Chunking [EXISTING]
  3. Citation Extraction [NEW]
  4. Entity Extraction [NEW]
  5. Relation Extraction [NEW]
  6. Paper Graph Builder [NEW]
  7. Neo4j Storage [NEW]
  8. Embeddings [EXISTING]
  9. Qdrant upsert [EXISTING]
```

---

### 4. OCR & Text Extraction (`app/services/ocr.py`)

**Status:** ✅ **KEEP (with enhancements)**

**Current Implementation:**
- PyMuPDF (fitz) for page extraction + rendering
- doctr (document AI) for OCR per-page
- GPU support detection
- Returns: `list[(page_num, text)]`

**Strengths:**
- Page-by-page processing (memory efficient)
- Handles both PyMuPDF and doctr fallback
- GPU-aware

**Enhancements for GraphRAG (Phase 3):**
- Add structured section detection (Abstract, Introduction, Method, etc.)
- Add reference section extraction (without OCR for references)
- Return enhanced output:
  ```python
  {
    "title": str,
    "abstract": str,
    "sections": [{"heading": str, "text": str}],
    "references": [str],  # Raw references from PDF
    "pages": [(page_num, text)]  # Legacy
  }
  ```

---

### 5. Text Processing (`app/services/text_processing.py`)

**Status:** ⚠️ **KEEP + REFACTOR**

**Current Implementation:**
- `chunk_text(pages_text, doc_id)` — Splits pages into chunks

**Issue for GraphRAG:**
- Chunks lose section boundaries
- No metadata about section type (abstract, methods, etc.)

**Modifications (Phase 3):**
- Refactor to preserve section metadata
- Return chunks with `{text, page, section, section_type}`
- Use section awareness for entity/relation extraction context

---

### 6. Embeddings (`app/services/embeddings.py`)

**Status:** ✅ **KEEP**

**Current Implementation:**
- Sentence Transformers (local, free)
- Model: `all-MiniLM-L6-v2` (configurable)
- Lazy loading with global singleton

**Use in GraphRAG:**
- Keep for vector retrieval (Phase 9)
- Generate embeddings for paper abstracts, section summaries
- Generate embeddings for extracted claims/methods

---

### 7. Vector Store (`app/services/vector_store.py`)

**Status:** ✅ **KEEP**

**Current Implementation:**
- Qdrant client wrapper
- Methods: `upsert_vectors()`, `search_vectors()`
- Metadata preservation (doc_id, page, filename, etc.)

**Use in GraphRAG:**
- Keep for semantic search (Phase 9, 12)
- Extend metadata to include `node_type`, `node_name` (Phase 9)

---

### 8. LLM Service (`app/services/llm.py`)

**Status:** ✅ **KEEP**

**Current Implementation:**
- Support for Ollama (local) and OpenRouter (cloud)
- `llm_client.generate_response(prompt, system_prompt)`

**Use in GraphRAG:**
- Keep for answer generation (Phase 15)
- Will be extended for entity/relation extraction (Phase 5-6)

---

## File Structure Summary

```
docRAG_v3/
├── app/
│   ├── api/
│   │   ├── main.py                          [✅ KEEP + EXTEND]
│   │   └── routes.py                        [✅ KEEP + EXTEND]
│   ├── core/
│   │   └── config.py                        [✅ KEEP + EXTEND]
│   ├── services/
│   │   ├── ocr.py                           [✅ KEEP + ENHANCE]
│   │   ├── embeddings.py                    [✅ KEEP]
│   │   ├── text_processing.py               [⚠️ KEEP + REFACTOR]
│   │   ├── vector_store.py                  [✅ KEEP]
│   │   ├── llm.py                           [✅ KEEP]
│   │   │
│   │   # NEW FILES FOR GRAPHRAG:
│   │   ├── paper_parser.py                  [🆕 PHASE 3]
│   │   ├── citation_extractor.py            [🆕 PHASE 4]
│   │   ├── citation_normalizer.py           [🆕 PHASE 4]
│   │   ├── entity_extractor.py              [🆕 PHASE 5]
│   │   ├── relation_extractor.py            [🆕 PHASE 6]
│   │   └── vector_indexer.py                [🆕 PHASE 9]
│   │
│   ├── graph/
│   │   ├── ontology.py                      [🆕 PHASE 2]
│   │   ├── paper_graph_builder.py           [🆕 PHASE 7]
│   │   └── (other graph modules)
│   │
│   ├── storage/
│   │   ├── neo4j_client.py                  [🆕 PHASE 8]
│   │   └── graph_repository.py              [🆕 PHASE 8]
│   │
│   ├── retrieval/
│   │   ├── graph_retriever.py               [🆕 PHASE 11]
│   │   ├── vector_retriever.py              [🆕 PHASE 12]
│   │   ├── query_classifier.py              [🆕 PHASE 13]
│   │   ├── hybrid_retriever.py              [🆕 PHASE 13]
│   │   └── citation_expander.py             [🆕 PHASE 14]
│   │
│   ├── llm/
│   │   ├── context_builder.py               [🆕 PHASE 15]
│   │   └── answer_generator.py              [🆕 PHASE 15]
│   │
│   ├── pipeline/
│   │   └── paper_ingestion_pipeline.py      [🆕 PHASE 10]
│   │
│   └── worker/
│       ├── celery_app.py                    [✅ KEEP]
│       └── tasks.py                         [✅ KEEP + EXTEND]
│
├── frontend-angular/                        [✅ KEEP + EXTEND (Phase 17)]
├── tests/                                   [✅ KEEP + EXPAND]
├── docker-compose.yml                       [⚠️ ADD Neo4j SERVICE]
├── requirements.txt                         [⚠️ ADD neo4j, LLM libs]
└── docs/
    ├── repo_audit.md                        [THIS FILE]
    ├── architecture.md                      [🆕 PHASE 19]
    ├── setup.md                             [🆕 PHASE 19]
    └── decisions.md                         [🆕 PHASE 19]
```

---

## Key Insertion Points for GraphRAG

### 1. **PDF Ingestion Pipeline (Worker)**
   - **Current:** OCR → Chunk → Embed → Qdrant
   - **New:** Add graph extraction after OCR:
     - Citation extraction
     - Entity extraction
     - Relation extraction
     - Paper graph building
     - Neo4j storage
   - **File:** Extend `app/worker/tasks.py` → Create `app/pipeline/paper_ingestion_pipeline.py`

### 2. **API Routes**
   - **Current:** `/upload`, `/status`, `/chat`, `/health`
   - **New:** Add graph-aware endpoints
     - `POST /graph-query` — Query using graph
     - `GET /papers/{paper_id}/graph` — Fetch paper graph
   - **File:** Extend `app/api/routes.py` or create `app/api/routes/graph_query.py`

### 3. **Text Extraction Enhancement**
   - **Current:** OCR returns flat text by page
   - **New:** Structured metadata (sections, titles, abstracts)
   - **File:** Create `app/paper/parser.py` → Hook into `app/services/ocr.py`

### 4. **Chunking Strategy**
   - **Current:** Generic token-based chunking
   - **New:** Respect section boundaries when extracting entities
   - **File:** Refactor `app/services/text_processing.py`

---

## Dependencies to Add

**For Graph Storage:**
```
neo4j==5.13.0  # Python driver
py2neo==2024.1.0  # Optional ORM layer
```

**For NLP / Extraction:**
```
spacy==3.7.2  # For NER (entity recognition)
networkx==3.2  # For graph algorithms
langchain==0.1.0  # Optional for orchestration
```

**Update docker-compose.yml:**
```yaml
neo4j:
  image: neo4j:5.13-community
  ports:
    - "7687:7687"    # Bolt protocol
    - "7474:7474"    # Browser UI
    - "7473:7473"    # HTTPS (optional)
  environment:
    NEO4J_AUTH: neo4j/password
    NEO4J_PLUGINS: "[\"apoc\"]"
  volumes:
    - neo4j_data:/var/lib/neo4j/data

volumes:
  neo4j_data:
```

---

## Acceptance Criteria for Phase 1

✅ **Completed:**
1. Current document ingestion path is clearly documented (see Architecture section)
2. Insertion points for graph pipeline identified (see Key Insertion Points section)
3. Reusable vs. new modules categorized (see File Structure Summary)
4. Dependencies identified (see Dependencies section)
5. Docker Compose update identified (add Neo4j service)

---

## Next Steps

1. **Phase 2:** Define graph ontology (`app/graph/ontology.py`)
   - Node types: Paper, Method, Dataset, Task, Metric, Author, Institution, Claim, Experiment, Section
   - Edge types: CITES, INTRODUCES, USES_DATASET, SOLVES_TASK, etc.

2. **Phase 3:** Build paper parser (`app/paper/parser.py`)
   - Extract structured sections, abstract, references
   - Build on top of enhanced OCR output

3. **Phase 4:** Citation extraction (`app/citations/extractor.py`)
   - Extract reference metadata (title, authors, year, DOI, arXiv ID)

4. **Phases 5-6:** Entity and relation extraction
   - Use LLM or spaCy for recognition
   - Build on section-aware chunking

---

## Notes

- **Keep Qdrant integration:** Vector search is still valuable for semantic context.
- **Backward compatibility:** Existing `/chat` endpoint can work alongside graph queries.
- **Frontend-agnostic:** Graph pipeline operates backend-only; frontend updates in Phase 17.
- **Testing:** Each new phase should include unit tests (tests directory exists and is ready).

---

**Audit Completed By:** Code Audit Agent  
**Repository:** AnshulPatil2005/docRAG_v3  
**Commit:** 8d6a7fe8af5be80e7ee17671c0fa92dc30a88940
