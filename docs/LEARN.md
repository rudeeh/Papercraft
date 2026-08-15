# Learning DocRAG: A Guide to the Architecture and Codebase

Welcome! This guide is designed to help you understand how this project is built, the technologies used, and the logic behind the code. Since you're interested in learning **FastAPI**, **Celery**, **Vector Databases**, and **Graphs**, this is the perfect place to start.

---

## 1. High-Level Architecture

The system is built using a "decoupled" architecture, meaning different parts of the system handle different responsibilities.

### The Stack:
- **FastAPI**: The "brain" of the operation. It handles incoming web requests (HTTP), validates data, and orchestrates other services.
- **Celery + Redis**: The "worker". Extracting text from PDFs (OCR) and building graphs takes time. We don't want the user to wait for a 5-minute process. FastAPI sends a "task" to Redis, and a Celery worker picks it up and processes it in the background.
- **Qdrant**: The "semantic memory". It stores text as "vectors" (mathematical representations of meaning), allowing us to search for information based on *intent* rather than just keywords.
- **Neo4j**: The "relational memory". While Qdrant knows what things *mean*, Neo4j knows how they are *connected* (e.g., "Paper A cites Paper B").
- **doctr**: The "eyes". It uses machine learning to "read" PDF files and turn pixels into text.

---

## 2. FastAPI: The Core Concepts

FastAPI is modern, fast, and built on Python type hints.

### A. Routes and Routers (`app/api/`)
Instead of putting all code in one file, we use `APIRouter`.
- Look at `app/api/main.py`: It initializes the app and "includes" the router from `app/api/routes.py`.
- This keeps the code clean and organized.

### B. Pydantic Models
FastAPI uses **Pydantic** for data validation. If you send a request with a string where the API expects an integer, Pydantic will automatically catch the error and return a helpful message.

### C. Async/Await
You'll see `async def` everywhere. This allows FastAPI to handle thousands of concurrent connections. While one request is waiting for a database response, the CPU can work on another request.

---

## 3. Code Walkthrough: Phases 1–6

### Phase 2: The Ontology (`app/graph/ontology.py`)
**Ontology** is just a fancy word for "a map of what things exist and how they connect."
- **Nodes**: Entities like `Paper`, `Method`, `Dataset`.
- **Edges**: Relationships like `CITES`, `USES_DATASET`.
- **Logic**: We use Python `Enum`s to strictly define these, so we don't accidentally create a relationship that doesn't make sense (like a "Dataset" citing a "Method").

### Phase 3: The Paper Parser (`app/paper/parser.py`)
When `doctr` extracts text, it's just a long string. The Parser's job is to:
1. Identify the **Title**.
2. Identify the **Abstract**.
3. Split the text into **Sections** (Introduction, Conclusion, etc.).
4. Find the **References** at the end.

### Phase 4: Citation Extraction (`app/citations/`)
This uses **Regular Expressions (Regex)**.
- `extractor.py`: Looks for patterns like `(2021)`, `arXiv:2104.XXXXX`, or `DOI: 10.1101/...`.
- `normalizer.py`: Ensures that "John Doe (2020)" and "Doe, J. 2020" are recognized as the same thing. This is crucial for building a clean graph.

### Phase 5: Entity Extraction (`app/graph/entity_extractor.py`)
This extracts important research concepts/entities (nodes) from the text.
- To stay highly efficient, it uses robust, deterministic heuristics and regexes (e.g., matching common method suffixes like `model` or `architecture`, or known list terms like `Transformer` or `ImageNet`).

### Phase 6: Relation Extraction (`app/graph/relation_extractor.py`)
This extracts semantic connections (edges) between those nodes in the text.
- **Linguistic Regex Heuristics**: It scans for sentences containing *two or more* of our extracted entities, and looks for semantic connector phrases (like `outperforms`, `evaluated on`, `extends`).
- **Ontology Normalization Mapping**: Natural language relations are normalized to match the strict central ontology. For instance, if a `Method` is evaluated on a `Dataset`, the natural-language relationship "evaluated on" is automatically mapped to `USES_DATASET` to remain strictly compliant with the ontology rules.

---

## 4. How to Learn by Exploring

To understand how a request flows through the system:
1. **Start at `app/api/routes.py`**: Find the `/upload` endpoint.
2. **Follow the flow**: See how it calls a service, which then triggers a Celery task in `app/worker/tasks.py`.
3. **Look at the Models**: Check `app/core/config.py` to see how environment variables are managed.

### Exercises for you:
- **Try adding a new Node Type** to `app/graph/ontology.py` (e.g., `Conference`).
- **Look at a test** in `tests/test_parser.py`. Try running it with `pytest` and see how it validates the code.
- **Check `scripts/health_check.sh`** to see how we automate the verification of the whole stack.

---

Next, we will be moving to **Phase 7: Paper Graph Builder**, where we convert extracted entities and relations into a single integrated graph representation for each paper!
