# Papercraft # 

> An open-source, living knowledge graph that connects papers, methods, datasets, code, models, experiments, claims, and ideas. Researchers explore, compare,and
reason across the entire research ecosystem instead of reading isolated PDFs.
> 
## Table of Contents

1. [Vision & Philosophy](#vision--philosophy)
2. [System Architecture](#system-architecture)
3. [Core Ontology](#core-ontology)
4. [Phase-by-Phase Implementation](#phase-by-phase-implementation)
5. [Technology Stack](#technology-stack)
6. [API Specification](#api-specification)
7. [Data Flow](#data-flow)
8. [Development Setup](#development-setup)
9. [Contributing](#contributing)
10. [Roadmap](#roadmap)
11. [License](#license)

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

### REST Endpoints

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

```bash
# 1. Clone the repository
git clone https://github.com/your-org/research-os.git
cd research-os

# 2. Start infrastructure services
docker-compose -f docker-compose.infra.yml up -d
# This starts: Neo4j, PostgreSQL, Redis, MinIO, Weaviate

# 3. Install backend dependencies
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Run database migrations
alembic upgrade head

# 5. Start the API
uvicorn app.main:app --reload --port 8000

# 6. Install frontend dependencies (new terminal)
cd ../web
npm install

# 7. Start the web app
npm run dev

# 8. Open http://localhost:3000
```

### Environment Variables

Create `.env` from `.env.example`:

```bash
# Database
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=researchos

POSTGRES_URI=postgresql://researchos:researchos@localhost:5432/researchos

REDIS_URI=redis://localhost:6379/0

# Storage
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_PDFS=researchos-pdfs

# AI Services
OPENAI_API_KEY=sk-...
OPENALEX_API_URL=https://api.openalex.org

# Vector DB
WEAVIATE_URL=http://localhost:8080

# App
SECRET_KEY=your-secret-key
DEBUG=true
```

### Project Structure

```
research-os/
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── release.yml
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI entry point
│   │   ├── api/
│   │   │   ├── v1/
│   │   │   │   ├── ingest.py
│   │   │   │   ├── documents.py
│   │   │   │   ├── search.py
│   │   │   │   └── graphql.py
│   │   │   └── deps.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   └── logging.py
│   │   ├── models/                 # SQLAlchemy models
│   │   ├── services/
│   │   │   ├── parser/
│   │   │   │   ├── marker_client.py
│   │   │   │   └── openalex_client.py
│   │   │   ├── extraction/
│   │   │   │   ├── entity_extractor.py
│   │   │   │   ├── relation_extractor.py
│   │   │   │   └── confidence_scorer.py
│   │   │   ├── graph/
│   │   │   │   ├── neo4j_client.py
│   │   │   │   └── schema.py
│   │   │   ├── search/
│   │   │   │   ├── vector_search.py
│   │   │   │   └── hybrid_ranker.py
│   │   │   └── curation/
│   │   │       ├── attestation.py
│   │   │       └── reputation.py
│   │   └── workers/
│   │       └── celery_app.py
│   ├── alembic/                    # Database migrations
│   ├── tests/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── pyproject.toml
├── web/
│   ├── app/
│   │   ├── (routes)/
│   │   │   ├── page.tsx            # Home / search
│   │   │   ├── paper/
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx
│   │   │   ├── explorer/
│   │   │   │   └── page.tsx
│   │   │   └── curate/
│   │   │       └── page.tsx
│   │   ├── components/
│   │   │   ├── graph/
│   │   │   │   ├── ForceGraph.tsx
│   │   │   │   ├── MethodTree.tsx
│   │   │   │   └── ContradictionView.tsx
│   │   │   ├── pdf/
│   │   │   │   ├── PDFViewer.tsx
│   │   │   │   └── TextHighlighter.tsx
│   │   │   ├── search/
│   │   │   │   ├── SearchBar.tsx
│   │   │   │   └── FilterPanel.tsx
│   │   │   └── curation/
│   │   │       ├── ClaimEditor.tsx
│   │   │       └── AttestationButton.tsx
│   │   ├── lib/
│   │   │   ├── api.ts
│   │   │   ├── graphql-client.ts
│   │   │   └── utils.ts
│   │   └── types/
│   │       └── index.ts
│   ├── public/
│   ├── Dockerfile
│   ├── package.json
│   └── next.config.js
├── services/
│   ├── marker/                     # Marker PDF parser service
│   │   ├── Dockerfile
│   │   ├── app.py
│   │   └── requirements.txt
│   └── weaviate/                   # Weaviate schema & config
│       └── schema.json
├── schemas/
│   ├── document_v1.json            # Canonical Document Schema
│   ├── graphql_schema.graphql
│   └── ontology.cypher             # Neo4j schema definitions
├── docker-compose.yml
├── docker-compose.infra.yml
├── Makefile
├── README.md
├── ROADMAP.md
├── CONTRIBUTING.md
└── LICENSE
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
