# 📁 Papercraft File Structure Guide

A beginner-friendly guide to understanding the repository layout.

---

## 🎯 Quick Overview

**Papercraft** is a research paper management and analysis system with:
- **Backend API** (Python/FastAPI) - handles data, embeddings, and LLM processing
- **Frontend** (Next.js/TypeScript) - user interface for interacting with papers
- **Database** - SQLAlchemy models and Alembic migrations
- **Documentation** - guides, architecture, and setup instructions

---

## 📂 Root Level Files

```
├── README.md                          # Main project documentation
├── LICENSE                            # MIT License
│
├── docker-compose.yml                 # Local development setup (all services)
├── Dockerfile                         # Backend containerization
├── .dockerignore                      # Files to exclude from Docker build
├── .gitignore                         # Git ignore rules
│
├── requirements.txt                   # Python dependencies
├── alembic.ini                        # Database migration configuration
├── pytest.ini                         # Testing configuration
│
├── Makefile                           # Quick commands (make build, etc.)
└── .env.example                       # Template for environment variables
```

---

## 🏗️ Core Directory Structure

### 📦 `/app` - Backend Application (Python)
The heart of the system - all business logic lives here.

```
app/
├── __init__.py                        # Package initialization
│
├── api/                               # 🔌 API Endpoints
│   └── REST routes and request handlers
│
├── core/                              # ⚙️ Core Configurations
│   └── Settings, constants, and core utilities
│
├── db/                                # 🗄️ Database Layer
│   ├── Models (SQLAlchemy)
│   ├── Schemas (Pydantic for validation)
│   └── Database connection setup
│
├── services/                          # 🛠️ Business Logic
│   ├── Paper management
│   ├── User management
│   └── High-level operations
│
├── llm/                               # 🤖 LLM Integration
│   └── Language model API calls and prompting
│
├── embeddings/                        # 🔍 Embedding Management
│   └── Text embedding generation and storage
│
├── retrieval/                         # 📚 RAG (Retrieval-Augmented Generation)
│   └── Semantic search and document retrieval
│
├── citations/                         # 📖 Citation Processing
│   └── Extract and manage paper citations
│
├── graph/                             # 📊 Knowledge Graph
│   └── Paper relationships and connections
│
├── paper/                             # 📄 Paper Processing
│   └── Parse, extract, and process paper content
│
├── pipeline/                          # 🔄 Data Processing Pipelines
│   └── Workflows for ingesting and processing papers
│
├── worker/                            # ⏱️ Background Jobs
│   └── Async task processing (Celery/similar)
│
├── storage/                           # 💾 File Storage
│   └── Handle PDF and document storage
│
└── evaluation/                        # 📊 Testing & Evaluation
    └── Evaluation metrics and test utilities
```

**What this means for beginners:**
- Start with `/api` to understand what endpoints exist
- Check `/services` for main functionality
- Look at `/db/models.py` to understand data structure
- Use `/llm` and `/embeddings` to see AI integration

---

### 🌐 `/web` - Frontend Application (Next.js/TypeScript)
The user-facing interface.

```
web/
├── app/                               # 🎨 Page Components (App Router)
│   ├── page.tsx                       # Homepage
│   ├── api/                           # Backend API route handlers
│   └── [routes]/                      # Dynamic pages
│
├── lib/                               # 🔧 Utilities & Helpers
│   ├── API client functions
│   ├── Type definitions
│   └── Reusable utilities
│
├── public/                            # 📷 Static Assets
│   └── Images, icons, fonts
│
├── package.json                       # Node.js dependencies
├── package-lock.json                  # Locked dependency versions
├── tsconfig.json                      # TypeScript configuration
├── tailwind.config.ts                 # Tailwind CSS setup
├── postcss.config.js                  # CSS processing
├── next.config.mjs                    # Next.js configuration
└── Dockerfile                         # Frontend containerization
```

**What this means for beginners:**
- `/app` is where pages are created
- `/lib` contains reusable code (don't repeat yourself!)
- Modify UI components here

---

### 📚 `/docs` - Documentation
Guides and reference materials.

```
docs/
├── architecture.md                    # System design & overview
├── setup.md                           # Installation & setup guide ⭐ START HERE
├── decisions.md                       # Why we built things this way
├── arxiv-ingest.md                    # How papers are ingested from arXiv
├── DOCRAG_README.md                   # Document RAG system details
├── AGENT_PHASES.md                    # Agent development phases
├── FILE_STRUCTURE.md                  # This file (repo layout reference)
├── LEARN.md                           # Learning guide for newcomers
│
├── PHASE_1_3_EVALUATION.md            # Evaluation results
├── PHASE_1_3_TEST_REPORT.md           # Test reports
├── FINAL_STATUS_PHASE_1_3.md          # Project status
│
└── repo_audit.md                      # Code quality audit
```

**Quick start:** Read `setup.md` first, then `architecture.md`

---

### 🧪 `/tests` - Test Suite
Automated tests for quality assurance.

```
tests/
└── Unit tests, integration tests, and test utilities
```

---

### 🔄 `/alembic` - Database Migrations
Version control for database schema changes.

```
alembic/
├── versions/                          # Migration files (auto-generated)
└── Configuration for database schema evolution
```

**When to use:** When you modify database models, run `alembic revision --autogenerate -m "description"`

---

### 📋 `/scripts` - Utility Scripts
Helper scripts for development and deployment.

```
scripts/
├── create_test_pdf.py                 # Generate test PDFs
├── health_check.sh                    # Check service health
├── load_small_pdf.sh                  # Load test document (small)
├── load_large_pdf.sh                  # Load test document (large)
└── query_rag.sh                       # Test RAG functionality
```

**Usage:** `./scripts/health_check.sh` to verify services are running

---

### 🎬 `/frontend-angular` - Legacy Frontend (Optional)
Older Angular-based frontend (may be deprecated).

---

### 📑 `/papers` - Sample Papers
Example research papers for testing.

---

### 📦 Root Bundle Files (Advanced)
```
papercraftmain.bundle                  # Serialized data/config snapshot
papercraftweaviateswap.bundle          # Weaviate vector DB snapshot
```

---

## 🔍 How to Navigate as a Beginner

### **I want to add a new API endpoint**
1. Look at existing endpoints in `/app/api/`
2. Add route in `/app/api/`
3. Add business logic in `/app/services/`
4. Reference database models in `/app/db/models.py`

### **I want to modify the UI**
1. Find the page in `/web/app/`
2. Update components and styling
3. Use functions from `/web/lib/` for API calls

### **I want to understand the architecture**
1. Read `/docs/setup.md` (get it running)
2. Read `/docs/architecture.md` (understand design)
3. Explore `/app/core/` and `/app/db/` (understand core concepts)

### **I want to run the application**
1. Follow `/docs/setup.md`
2. Use `docker-compose up` or follow manual setup
3. Run `/scripts/health_check.sh` to verify

### **I want to add a database model**
1. Modify `/app/db/models.py`
2. Run `alembic revision --autogenerate -m "your description"`
3. Run `alembic upgrade head`

---

## 📊 Technology Stack at a Glance

| Layer | Technology | Location |
|-------|-----------|----------|
| **Backend** | Python, FastAPI | `/app` |
| **Frontend** | Next.js, TypeScript | `/web` |
| **Database** | SQLAlchemy (ORM), Alembic (migrations) | `/app/db`, `/alembic` |
| **AI/ML** | LLM APIs, Embeddings, RAG | `/app/llm`, `/app/embeddings`, `/app/retrieval` |
| **Containerization** | Docker | `Dockerfile`, `docker-compose.yml` |
| **Testing** | Pytest | `/tests`, `pytest.ini` |

---

## 🚀 Next Steps

1. **Read** → `/docs/setup.md` to get started
2. **Run** → `docker-compose up` to start services
3. **Explore** → Check `/app/api/` to see available endpoints
4. **Code** → Start with small changes in `/web/app/` or `/app/services/`
5. **Test** → Use `/scripts/` to test functionality

---

## 💡 Pro Tips

- Use `Makefile` for quick commands: `make help`
- Check `.env.example` to understand required environment variables
- Read `docs/LEARN.md` for project-specific learning materials
- Look at existing code before writing new features (follow patterns!)
- Database schema is the "source of truth" → check `/app/db/models.py` first

---

## ❓ Common Questions

**Q: Where do I find the main entry point?**
A: Check `main.py` or `app.py` in the root `/app` directory, and check `web/app/page.tsx` for frontend.

**Q: How do I add a new feature?**
A: 1) Plan in docs 2) Update models if needed 3) Create API endpoint 4) Update frontend 5) Write tests

**Q: Where are environment variables configured?**
A: Copy `.env.example` to `.env` and fill in your values.

**Q: How do I debug issues?**
A: Check `/docs/` for troubleshooting, use `docker-compose logs [service]`, read code comments.

---

Happy coding! 🎉
