# Papercraft # 

> An open-source, living knowledge graph that connects papers, methods, datasets, code, models, experiments, claims, and ideas. Researchers explore, compare,and
reason across the entire research ecosystem instead of reading isolated PDFs.
> 
## Table of Contents

1. [Implementation Status](#implementation-status)
2. [Vision & Philosophy](#vision--philosophy)
3. [System Architecture](#system-architecture)
4. [Core Ontology](#core-ontology)
5. [Phase-by-Phase Implementation](#phase-by-phase-implementation)
6. [Technology Stack](#technology-stack)
7. [API Specification](#api-specification)
8. [Data Flow](#data-flow)
9. [Development Setup](#development-setup)
10. [Contributing](#contributing)
11. [Roadmap](#roadmap)
12. [License](#license)

---

## Implementation Status

**This README is the specification.** Most of it describes where Papercraft
is going; this section describes where it actually is, so a reader can tell
the two apart. Anything not listed as built is a plan.

### Built and tested

| Area | State |
|---|---|
| Ingestion pipeline | PDF → OCR → parse → citations → entities → relations → graph build → Neo4j → chunk → embed → Weaviate, every step non-critical and independently recoverable |
| Graph ontology | 10 node types, 20 edge types, `VALID_EDGES` enforced on every write |
| Metadata enrichment | OpenAlex, resolved by DOI → arXiv ID → fuzzy title, with the match method reported |
| Extraction | Deterministic heuristics by default; opt-in two-stage LLM pass (`EXTRACTION_PROVIDER=llm\|hybrid`) |
| Confidence routing | auto-insert / draft / manual / discarded, with independent agreement between passes as the strongest signal |
| Curation | Draft queue, attestation voting, promotion into the graph, reputation, append-only audit log |
| Retrieval | Query classifier → graph / vector / hybrid, plus citation expansion |
| Answer generation | Grounded answers with sources and the graph facts used |
| Auth | Register / login / me, JWT bearer, optional by default (single-player mode) |
| Storage | Neo4j, Weaviate, PostgreSQL, Redis, MinIO — every one of them optional at the pipeline level |
| API | REST under `/api/v1` (see [API Specification](#api-specification)) |
| Frontends | Angular (mature: upload, ask, citation explorer) and Next.js (ingest, ask, review queue) |
| Evaluation | `evaluation/run_eval.py` compares graph-only / vector-only / hybrid over `questions.json` |
| Tests | **577 unit tests, all passing.** No Docker services required — Neo4j, Weaviate, OpenAlex and the LLM are mocked; PostgreSQL is substituted by in-memory SQLite |

### Specified but not built

- **GraphQL API** (Strawberry) — REST only today
- **arXiv / PubMed / GitHub / Hugging Face ingestion** — upload only; no
  `POST /ingest/arxiv`, no daily sync
- **Marker / Nougat / GROBID parsing** — PyMuPDF with a doctr OCR fallback
- **SPECTER2 embeddings** — `all-MiniLM-L6-v2` by default
- **ORCID sign-in** — the `orcid` column exists; no identity provider is contacted
- **Export formats** (BibTeX, CSV, RDF), **Zotero plugin**, **Chrome extension**
- **Fine-tuned extraction models**, **table and equation extraction**
- **Kubernetes, Prometheus/Grafana, Loki**

### Known limitations

- **Fuzzy title matching cannot detect negation.** "Attention Is All You
  Need" and "Attention Is Not All You Need" score 0.93 similarity. The
  enriched record therefore carries `match_method` and `match_confidence`
  rather than presenting a guess as an identity; there is a test pinning
  this open.
- **Promotion does not block on Neo4j.** A draft promoted while the graph
  is unreachable is marked promoted anyway and the replay set is derivable
  from the audit log — but nothing runs that replay automatically yet.
- **Two frontends.** The Angular app is the mature one. `web/` is the
  spec's target and should eventually replace it.

Every one of these choices is written up with its cost in
[`docs/decisions.md`](./docs/decisions.md).

---

## Vision & Philosophy

### The Problem

Today's scientific knowledge is trapped inside static PDFs. A researcher reading a paper in isolation cannot:
- See if a claim has been contradicted by later work
- Trace the lineage of a method across dozens of papers
- Compare experiments that use the same dataset but different methods
- Discover code implementations linked to a specific technique

### The Solution

**ResearchOS** treats scientific artifacts as first-class nodes in a queryable knowledge graph:

- **Papers** are not documents—they are containers of claims, methods, and experiments
- **Claims** are atomic, attributable, and linked to supporting or contradicting evidence
- **Methods** have lineages: who introduced them, who extended them, who contradicted them
- **Datasets** carry provenance: which papers used them, for what tasks, with what results
- **Code** is linked to the exact claims and experiments it reproduces

### Design Principles

1. **Open by Default**: All data, schemas, and APIs are open. The graph belongs to science.
2. **Attribution Over Authority**: Every extracted claim traces back to who extracted it and from which paper. Controversy is visible, not resolved.
3. **Human-in-the-Loop**: AI extracts structure; humans attest, correct, and curate. Trust is earned through provenance.
4. **Single-Player Mode First**: The graph must be useful to an individual researcher building their literature review before it becomes valuable as a network.
5. **Interoperability**: Export everything (BibTeX, CSV, subgraph JSON, RDF). No lock-in.

---

## System Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │  Web Application │  │ Chrome Extension │  │  GraphQL API Consumers   │  │
│  │  (Next.js/React) │  │ (PDF Sidebar)    │  │  (Third-party tools)     │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            API GATEWAY (FastAPI)                             │
│  • Authentication (OAuth2 + ORCID)                                           │
│  • Rate Limiting & Caching (Redis)                                           │
│  • GraphQL Endpoint (Strawberry)                                             │
│  • REST Endpoints for Ingestion & Search                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
┌─────────────────────────────────────────────────────────────────────────────┐
│                          APPLICATION SERVICES                                │
│  ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────────┐  │
│  │   Search Service   │  │ Extraction Pipeline│  │   Curation Engine    │  │
│  │  (Hybrid: Vector   │  │  (Async: Celery +  │  │  (Attestation,       │  │
│  │   + Graph + Text)  │  │   RabbitMQ/Redis)  │  │   Reputation, Diff)  │  │
│  └────────────────────┘  └────────────────────┘  └──────────────────────┘  │
│  ┌────────────────────┐  ┌────────────────────┐                            │
│  │  Ingestion Engine  │  │  Reference Resolver│                            │
│  │  (arXiv, PubMed,   │  │  (OpenAlex,        │                            │
│  │   GitHub, HF)      │  │   Crossref)        │                            │
│  └────────────────────┘  └────────────────────┘                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │   Graph DB       │  │   Vector DB      │  │   Relational DB          │  │
│  │   (Neo4j /       │  │   (Weaviate /    │  │   (PostgreSQL:           │  │
│  │    Memgraph)     │  │    Qdrant)       │  │    Users, Jobs, Audit    │  │
│  │   • Canonical    │  │   • Embeddings   │  │    Logs, Votes)          │  │
│  │     Graph        │  │   • Semantic     │  │                          │  │
│  │   • Traversals   │  │     Search       │  │                          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────┐                                │
│  │   Object Store   │  │   Cache Layer    │                                │
│  │   (MinIO / S3)   │  │   (Redis)        │                                │
│  │   • PDFs         │  │   • Sessions     │                                │
│  │   • Snapshots    │  │   • Hot Graph    │                                │
│  │   • Exports      │  │   • Job Queues   │                                │
│  └──────────────────┘  └──────────────────┘                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Service Descriptions

#### 1. Ingestion Engine
- **Purpose**: Acquire papers and code artifacts from external sources
- **Sources**: arXiv (CS.CV initial wedge), PubMed Central, GitHub API, Hugging Face Hub
- **Output**: Raw PDFs + metadata blobs queued for parsing

#### 2. Parsing Pipeline (Document Processing)
- **Purpose**: Convert messy PDFs into structured, machine-readable documents
- **Primary Parser**: Marker (vision-based PDF → Markdown)
- **Metadata Enrichment**: OpenAlex API (authors, affiliations, citations, DOI resolution)
- **Output**: Canonical Document Model (JSON)

#### 3. Extraction Pipeline (Entity & Relation Extraction)
- **Purpose**: Transform structured documents into graph entities
- **Method**: Two-stage LLM pipeline (Entity Extraction → Relation Extraction)
- **Human Review**: Confidence-based routing (auto-insert / draft / manual queue)
- **Output**: Graph-ready nodes and edges with provenance

#### 4. Graph Database (Neo4j)
- **Purpose**: Store and query the canonical knowledge graph
- **Schema**: Strict core ontology (20 entity types, 30 relationship types)
- **Queries**: Cypher for traversals, GraphQL for client consumption

#### 5. Vector Database (Weaviate)
- **Purpose**: Semantic search over claims, methods, and paper sections
- **Embeddings**: SPECTER2 or domain-tuned sentence transformers
- **Hybrid Search**: Vector similarity + graph filter + keyword boost

#### 6. Curation Engine
- **Purpose**: Community attestation, reputation, and conflict resolution
- **Features**: Claim editing, attestation voting, contradiction linking, diff views
- **Incentive**: Reputation scores for curators

---

## Core Ontology

### Entity Types (Nodes)

```
Paper
├── id: string (arxiv:2401.12345)
├── title: string
├── authors: list[Author]
├── year: int
├── venue: string
├── doi: string
├── abstract: string (markdown)
├── pdf_url: string
└── metadata_source: string (openalex / crossref)

Claim
├── id: uuid
├── text: string (markdown)
├── type: enum [causal, correlational, descriptive, methodological]
├── confidence: float (0.0 - 1.0)
├── section_origin: string (Introduction / Methods / Results / Discussion)
├── paper_id: string
├── extracted_by: string (model_version)
├── attested_by: list[user_id]
└── extraction_confidence: float

Method
├── id: uuid
├── name: string
├── description: string (markdown)
├── implementation_urls: list[string]
├── parent_method_id: uuid (nullable)
├── introduced_in: string (paper_id)
└── domain_tags: list[string]

Dataset
├── id: uuid
├── name: string
├── domain: string
├── size: string
├── benchmark_tasks: list[string]
├── license: string
└── source_url: string

Experiment
├── id: uuid
├── hypothesis: string
├── independent_variables: list[string]
├── dependent_variables: list[string]
├── sample_size: int
├── statistical_test: string
├── paper_id: string
└── results_summary: string

CodeArtifact
├── id: uuid
├── repo_url: string
├── framework: string
├── reproducibility_score: float
├── linked_paper_id: string
├── linked_claim_ids: list[uuid]
└── linked_method_ids: list[uuid]
```

### Relationship Types (Edges)

| Edge | Source | Target | Properties |
|------|--------|--------|------------|
| `MAKES_CLAIM` | Paper | Claim | section: string, confidence: float |
| `SUPPORTED_BY` | Claim | Experiment | strength: enum [strong, moderate, weak] |
| `CONTRADICTED_BY` | Claim | Claim | explanation: string, attested_by: list |
| `USES_METHOD` | Experiment | Method | version: string, modifications: string |
| `USES_DATASET` | Experiment | Dataset | split: string, preprocessing: string |
| `EXTENDS` | Method | Method | description: string, paper_id: string |
| `CONTRADICTS` | Method | Method | context: string |
| `INTRODUCED_IN` | Method | Paper | — |
| `CITES` | Paper | Paper | citation_context: string, section: string |
| `IMPLEMENTS` | CodeArtifact | Method | coverage: float |
| `REPRODUCES` | CodeArtifact | Experiment | reproducibility_notes: string |

### Provenance Model

Every edge in the graph carries provenance:

```json
{
  "edge_id": "uuid",
  "type": "SUPPORTED_BY",
  "source": "claim_uuid",
  "target": "experiment_uuid",
  "provenance": {
    "extracted_by": "extraction-v0.1.0-gpt4o",
    "extracted_at": "2026-08-04T12:00:00Z",
    "attested_by": ["user_uuid_1", "user_uuid_2"],
    "attestation_score": 0.92,
    "last_verified_at": "2026-08-04T15:00:00Z"
  }
}
```

---

## Phase-by-Phase Implementation

### Phase 0: Foundation (Weeks 1–2)
**Goal**: JSON flowing from PDF to graph. One end-to-end path.

| Task | Owner | Deliverable |
|------|-------|-------------|
| Set up Docker Compose (Neo4j, PostgreSQL, Redis, MinIO) | Both | `docker-compose.yml` |
| Define Canonical Document JSON Schema | Both | `schemas/document_v1.json` |
| Build Marker parsing service (FastAPI wrapper) | Engineer A | `services/parser/` |
| Build OpenAlex metadata enricher | Engineer A | `services/metadata/` |
| Build Neo4j schema + basic CRUD | Engineer B | `services/graph/` |
| Build D3 force graph explorer (hardcoded data) | Engineer B | `web/app/explorer/` |
| End-to-end test: 10 arXiv CV papers | Both | Demo video |

**Success Criteria**:
- [ ] `POST /ingest {arxiv_id}` returns Canonical Document JSON in <30 seconds
- [ ] JSON ingestion creates Paper + Section nodes in Neo4j
- [ ] Explorer renders interactive force graph of 10 papers

---

### Phase 1: MVP (Weeks 3–8)
**Goal**: A researcher can upload papers, see extracted claims, and explore method lineage.

#### 1.1 Ingestion & Parsing
| Task | Details |
|------|---------|
| arXiv daily sync | Cron job fetches new cs.CV papers |
| Batch processing | Celery workers process PDFs asynchronously |
| PDF viewer | React component with text layer for highlighting |
| Storage | MinIO for PDFs, PostgreSQL for job tracking |

#### 1.2 Extraction Pipeline
| Task | Details |
|------|---------|
| Section-aware chunking | Split by headings, preserve hierarchy |
| Entity extraction (LLM) | Few-shot prompt with 5 examples per entity type |
| Relation extraction (LLM) | Second pass: given entities, extract relationships |
| Confidence scoring | Per-extraction confidence, routing logic |
| Ground truth dataset | 20 hand-curated papers for evaluation |

#### 1.3 Graph Explorer
| Task | Details |
|------|---------|
| Paper detail view | Sections, claims, methods as cards |
| Method lineage view | Tree/graph of method evolution |
| Contradiction surfacing | Visual links between contradictory claims |
| Search | Keyword search over titles/abstracts |

#### 1.4 Curation (Human-in-the-Loop)
| Task | Details |
|------|---------|
| Highlight-to-claim | User selects text in PDF, tags as Claim/Method/Dataset |
| Attestation | Upvote/downvote auto-extracted entities |
| Edit & override | Correct extracted text, link to different entities |

**Success Criteria**:
- [ ] 500 papers ingested (cs.CV 2022–2024)
- [ ] Auto-extraction precision on Claims > 75%
- [ ] 50 human attestations
- [ ] 10 active beta users

---

### Phase 2: Intelligence (Weeks 9–14)
**Goal**: The graph answers research questions, not just displays papers.

#### 2.1 Semantic Search
| Task | Details |
|------|---------|
| Vector embeddings | SPECTER2 for paper sections and claims |
| Hybrid search | Vector similarity + graph traversal + keyword |
| Natural language queries | "Find papers using CLIP on medical datasets without fine-tuning" |

#### 2.2 Enhanced Extraction
| Task | Details |
|------|---------|
| Fine-tuned extraction model | Train on accumulated human attestations |
| Table extraction | Structured table → CSV/JSON |
| Equation extraction | LaTeX preservation, renderable math |
| Multi-paper synthesis | Cross-paper claim comparison |

#### 2.3 Reputation & Incentives
| Task | Details |
|------|---------|
| Reputation scoring | Points for attestations, corrections, contributions |
| Trusted curator badge | High-reputation users get weighted votes |
| Leaderboards | Top contributors per domain |

#### 2.4 API & Integrations
| Task | Details |
|------|---------|
| GraphQL API | Public API for third-party tools |
| Export formats | BibTeX, CSV, subgraph JSON, RDF |
| Zotero plugin | Import papers from Zotero into ResearchOS |

**Success Criteria**:
- [ ] 5,000 papers ingested
- [ ] 500 human attestations
- [ ] 100 weekly active users
- [ ] 1 third-party integration

---

### Phase 3: Ecosystem (Months 4–6)
**Goal**: ResearchOS becomes infrastructure that other tools build on.

| Feature | Description |
|---------|-------------|
| **Multi-domain support** | Expand beyond CV to NLP, robotics, biology |
| **GROBID integration** | Parallel parsing for citation context and bibliographic metadata |
| **Nougat fallback** | Math-heavy papers route to Nougat for equation fidelity |
| **Collaborative graphs** | Research groups build shared, private subgraphs |
| **Reproducibility tracking** | Link experiments to code runs, track reproducibility scores |
| **Claim verification** | Crowdsourced replication status for key claims |
| **Plugin system** | Third-party extensions for domain-specific extraction |

**Success Criteria**:
- [ ] 50,000 papers across 3+ domains
- [ ] 5,000 active users
- [ ] 10 third-party integrations
- [ ] Sustainable open-source community (10+ external contributors)

---

## Technology Stack

### Backend
| Component | Technology | Version |
|-----------|------------|---------|
| API Gateway | FastAPI | ^0.111 |
| GraphQL | Strawberry | ^0.235 |
| Task Queue | Celery + Redis | ^5.4 / ^7.2 |
| Graph Database | Neo4j (Community) | ^5.x |
| Vector Database | Weaviate | ^1.25 |
| Relational Database | PostgreSQL | ^16 |
| Object Storage | MinIO | ^2024 |
| Cache | Redis | ^7.2 |

### AI / ML
| Component | Technology | Purpose |
|-----------|------------|---------|
| PDF Parser | Marker | PDF → Markdown |
| Embeddings | SPECTER2 | Scientific text embeddings |
| LLM | GPT-4o / Claude 3.5 | Entity & relation extraction |
| Fine-tuning | LoRA (PEFT) | Domain-specific extraction models |

### Frontend
| Component | Technology | Version |
|-----------|------------|---------|
| Framework | Next.js | ^14 |
| UI Library | Tailwind CSS + shadcn/ui | ^3.4 |
| Graph Visualization | D3.js + Cytoscape.js | ^7.9 / ^3.26 |
| State Management | Zustand | ^4.5 |
| PDF Viewer | PDF.js (Mozilla) | ^4.3 |

### DevOps
| Component | Technology |
|-----------|------------|
| Containerization | Docker + Docker Compose |
| Orchestration | Kubernetes (Phase 2+) |
| CI/CD | GitHub Actions |
| Monitoring | Prometheus + Grafana |
| Logging | Loki + Grafana |

---

## API Specification

### Implemented today

Everything below this table is the target design; this is what the running
service actually serves. Interactive docs at `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness |
| `GET` | `/api/v1/llm-status` | Whether the server has an OpenRouter key |
| `POST` | `/api/v1/upload` | Upload a PDF; returns `doc_id` + `task_id` |
| `GET` | `/api/v1/status/{task_id}` | Per-step ingestion progress; 404 for an unknown ID |
| `POST` | `/api/v1/chat` | Legacy vector-RAG question answering |
| `POST` | `/api/v1/graph-query` | GraphRAG: answer + sources + retrieval trace |
| `GET` | `/api/v1/citation-graph` | The whole cross-paper citation network |
| `POST` | `/api/v1/auth/register` | Create a curator account, returns a bearer token |
| `POST` | `/api/v1/auth/login` | Exchange credentials for a bearer token |
| `GET` | `/api/v1/auth/me` | The acting user (anonymous when unauthenticated) |
| `GET` | `/api/v1/curation/drafts` | The review queue, lowest confidence first |
| `GET` | `/api/v1/curation/drafts/{id}` | One draft with its attestations |
| `POST` | `/api/v1/curation/drafts/{id}/attest` | Vote `+1` / `-1` |
| `POST` | `/api/v1/curation/drafts/{id}/promote` | Accept, and write into the graph |
| `POST` | `/api/v1/curation/drafts/{id}/reject` | Discard |
| `GET` | `/api/v1/curation/stats` | Queue counts by status |
| `GET` | `/api/v1/curation/leaderboard` | Curators by reputation |

Status codes carry meaning worth handling: `401` from `/chat` or
`/graph-query` means no OpenRouter key is available, `503` means a
datastore that request needed is unreachable, `409` means a draft is
already resolved, and `422` on a curation filter means the value was not a
recognised enum — deliberately not an empty list, which would read as
"the queue is clear".

### Target REST Endpoints

#### Ingestion
```http
POST /api/v1/ingest/arxiv
Content-Type: application/json

{
  "arxiv_id": "2401.12345",
  "priority": "normal",
  "callback_url": "https://example.com/webhook"
}

Response: 202 Accepted
{
  "job_id": "uuid",
  "status": "queued",
  "estimated_completion": "2026-08-04T12:00:30Z"
}
```

```http
GET /api/v1/ingest/status/{job_id}

Response: 200 OK
{
  "job_id": "uuid",
  "status": "completed",
  "document_id": "arxiv:2401.12345",
  "result_url": "/api/v1/documents/arxiv:2401.12345"
}
```

#### Documents
```http
GET /api/v1/documents/{document_id}

Response: 200 OK
{
  "document_id": "arxiv:2401.12345",
  "metadata": { ... },
  "structure": { ... },
  "content": { ... }
}
```

#### Search
```http
POST /api/v1/search
Content-Type: application/json

{
  "query": "vision transformers medical imaging",
  "filters": {
    "domain": "computer_vision",
    "year_range": [2022, 2024],
    "has_code": true
  },
  "limit": 20,
  "offset": 0
}
```

### GraphQL Schema (Excerpt)

```graphql
type Paper {
  id: ID!
  title: String!
  authors: [Author!]!
  year: Int!
  venue: String
  abstract: String
  claims: [Claim!]! @relationship(type: "MAKES_CLAIM", direction: OUT)
  methods: [Method!]! @relationship(type: "USES_METHOD", direction: OUT)
  citations: [Paper!]! @relationship(type: "CITES", direction: OUT)
  citedBy: [Paper!]! @relationship(type: "CITES", direction: IN)
}

type Claim {
  id: ID!
  text: String!
  type: ClaimType!
  confidence: Float!
  paper: Paper! @relationship(type: "MAKES_CLAIM", direction: IN)
  supportedBy: [Experiment!]! @relationship(type: "SUPPORTED_BY", direction: IN)
  contradictedBy: [Claim!]! @relationship(type: "CONTRADICTED_BY", direction: IN)
  attestationScore: Float!
}

type Method {
  id: ID!
  name: String!
  description: String!
  introducedIn: Paper! @relationship(type: "INTRODUCED_IN", direction: OUT)
  extendedBy: [Method!]! @relationship(type: "EXTENDS", direction: IN)
  extends: [Method!]! @relationship(type: "EXTENDS", direction: OUT)
  implementations: [CodeArtifact!]!
}

type Query {
  paper(id: ID!): Paper
  papers(filter: PaperFilter, limit: Int, offset: Int): [Paper!]!
  searchClaims(query: String!, domain: String): [Claim!]!
  methodLineage(methodId: ID!): [Method!]!
  contradictions(claimId: ID!): [Claim!]!
}
```

---

## Data Flow

### 1. Paper Ingestion Flow

```
User submits arXiv ID
        │
        ▼
┌───────────────┐
│  Ingestion    │  ← Validates ID, checks cache, queues job
│   Service     │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│  Job Queue    │  ← Celery task with Redis backend
│   (Celery)    │
└───────┬───────┘
        │
        ▼
┌───────────────┐     ┌───────────────┐
│    Marker     │────→│   OpenAlex    │
│   Parser      │     │    API        │
└───────┬───────┘     └───────┬───────┘
        │                     │
        └──────────┬──────────┘
                   ▼
        ┌───────────────────┐
        │  Canonical JSON   │  ← Merged Marker + OpenAlex output
        │   Assembler       │
        └─────────┬─────────┘
                  │
                  ▼
        ┌───────────────────┐
        │  Extraction       │  ← LLM entity & relation extraction
        │   Pipeline        │
        └─────────┬─────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
        ▼                   ▼
┌───────────────┐   ┌───────────────┐
│  Auto-insert  │   │  Draft Queue  │  ← Low-confidence extractions
│  (High conf)  │   │  (Human review)│
└───────┬───────┘   └───────────────┘
        │
        ▼
┌───────────────┐
│    Neo4j      │  ← Canonical graph updated
│   Graph DB    │
└───────────────┘
```

### 2. Query Flow

```
User query (NL or GraphQL)
        │
        ▼
┌───────────────┐
│  Query Parser │  ← NL → GraphQL translation (Phase 2)
│   / Router    │
└───────┬───────┘
        │
   ┌────┴────┐
   │         │
   ▼         ▼
┌──────┐  ┌────────┐
│Vector│  │ Graph  │
│Search│  │ Query  │
│(Weav)│  │(Neo4j) │
└──┬───┘  └───┬────┘
   │          │
   └────┬─────┘
        ▼
┌───────────────┐
│  Result       │  ← Merge, rank, deduplicate
│  Aggregator   │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│  Response     │  ← Papers, claims, methods with provenance
│  Formatter    │
└───────────────┘
```

---

## Development Setup

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 20+
- Git

### Quick Start

Everything runs from one compose file:

```bash
# 1. Clone
git clone https://github.com/rudeeh/Papercraft
cd Papercraft

# 2. Configure. The defaults work; the one worth setting is an
#    OpenRouter key, without which /graph-query answers 401 unless the
#    caller supplies their own.
cp .env.example .env

# 3. Bring up the stack. The first build is slow -- torch and doctr are
#    large -- and Neo4j's healthcheck gates the api and worker, so allow
#    ~30s after the build before the API answers.
docker compose up -d --build

# 4. Check it
curl http://localhost:8000/api/v1/health     # {"status":"ok"}

# 5. Ingest a paper
./scripts/load_small_pdf.sh                  # or: ./scripts/load_small_pdf.sh path/to/paper.pdf

# 6. Ask it something
curl -s -X POST http://localhost:8000/api/v1/graph-query \
  -H 'Content-Type: application/json' \
  -d '{"query": "What methods does this paper use?"}'
```

| URL | What |
|---|---|
| http://localhost:3000 | Next.js app — ingest, ask, review queue |
| http://localhost:8080 | Angular app — upload, ask, citation explorer |
| http://localhost:8000/docs | Interactive API docs |
| http://localhost:7474 | Neo4j browser (`neo4j` / `password`) |
| http://localhost:9001 | MinIO console (`minioadmin` / `minioadmin`) |

Running the backend outside Docker, running the tests, and the evaluation
harness are all covered in [`docs/setup.md`](./docs/setup.md).

### Environment Variables

`.env.example` is the authoritative list and ships with working defaults
for the compose stack. The ones worth knowing:

```bash
# LLM -- without a key, /chat and /graph-query 401 unless the request
# carries its own (the frontends offer a field for it).
OPENROUTER_API_KEY=sk-or-v1-...

# Extraction: heuristic (default, no LLM cost) | llm | hybrid
EXTRACTION_PROVIDER=heuristic
CONFIDENCE_AUTO_INSERT=0.85   # at or above this, straight into the graph
CONFIDENCE_DRAFT=0.50         # at or above this, queued for a human

# Auth. false = single-player mode: curate without signing up.
AUTH_REQUIRED=false
SECRET_KEY=dev-secret-change-me   # signs every token -- change it

# Enrichment. An address here joins OpenAlex's polite pool.
OPENALEX_ENABLED=true
OPENALEX_MAILTO=

# Object storage is off by default; uploads live in UPLOAD_DIR.
OBJECT_STORE_ENABLED=false
```

See [`docs/setup.md`](./docs/setup.md) for the full table.

### Project Structure

```
Papercraft/
├── .github/workflows/ci.yml     # pytest, pip check, requirements resolve, Next build
├── app/
│   ├── api/                     # FastAPI routers
│   │   ├── routes.py            #   upload / status / chat / health
│   │   ├── graph_routes.py      #   graph-query / citation-graph
│   │   ├── auth_routes.py       #   register / login / me
│   │   ├── curation_routes.py   #   draft queue, attest, promote, reject
│   │   └── deps.py              #   session + current-user resolution
│   ├── citations/               # reference extraction and normalisation
│   ├── core/                    # config, password hashing, tokens
│   ├── db/                      # SQLAlchemy models + engine
│   ├── embeddings/              # local | openai | stub embedders
│   ├── graph/
│   │   ├── ontology.py          #   node/edge types + validation
│   │   ├── entity_extractor.py  #   deterministic pass
│   │   ├── relation_extractor.py
│   │   ├── llm_extractor.py     #   two-stage LLM pass (opt-in)
│   │   ├── confidence_router.py #   auto-insert / draft / manual
│   │   └── paper_graph_builder.py
│   ├── llm/                     # context building, answer generation
│   ├── paper/parser.py          # title / abstract / sections / references
│   ├── pipeline/                # the ingestion orchestrator
│   ├── retrieval/               # graph, vector, hybrid, citation expansion
│   ├── services/                # ocr, embeddings, llm, openalex, curation
│   ├── storage/                 # neo4j, weaviate, object store, repositories
│   └── worker/                  # Celery app and tasks
├── docs/                        # architecture, setup, decisions (ADRs), audit
├── evaluation/                  # questions.json + run_eval.py
├── frontend-angular/            # the mature UI
├── web/                         # Next.js 14 app (ingest / ask / curate)
├── scripts/                     # load_small_pdf.sh, health_check.sh, ...
├── tests/                       # 577 unit tests
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Contributing

We welcome contributions from researchers, engineers, and designers. See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

### Quick Contribution Guide

1. **Fork** the repository
2. **Create a branch**: `git checkout -b feature/your-feature-name`
3. **Commit** with clear messages following [Conventional Commits](https://www.conventionalcommits.org/)
4. **Push** to your fork
5. **Open a Pull Request** with a clear description and screenshots if applicable

### Areas We Need Help

- **Domain experts**: Curate and attest extracted claims in your field
- **ML engineers**: Improve extraction accuracy, train domain-specific models
- **Frontend developers**: Build visualization components, improve accessibility
- **DevOps**: Kubernetes manifests, monitoring, performance optimization
- **Designers**: UX research, information architecture, visual design

## Acknowledgments

- [Marker](https://github.com/VikParuchuri/marker) by Vik Paruchuri for vision-based PDF parsing
- [OpenAlex](https://openalex.org/) for open bibliographic data
- [Neo4j](https://neo4j.com/) for graph database technology
- [Weaviate](https://weaviate.io/) for vector search
- The entire open-source scientific community

---

<p align="center">
  <strong>Built for researchers, by researchers.</strong><br>
  <a href="https://github.com/your-org/research-os">GitHub</a> •
  <a href="https://docs.researchos.org">Documentation</a> •
  <a href="https://discord.gg/researchos">Discord</a> •
  <a href="https://twitter.com/researchos">Twitter</a>
</p>
