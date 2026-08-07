# Research Paper GraphRAG System: Agent Execution Phases

## Project Goal

Build a research-paper intelligence system where each paper has its own internal knowledge graph, and all papers are connected through a global citation graph.

The system should support:

- Uploading research papers
- Extracting structured paper content
- Building a local graph for each paper
- Building a global citation graph across papers
- Combining graph retrieval and vector retrieval
- Answering research questions with citations

---

# Phase 1: Repository Audit and Setup

## Objective

Prepare the existing `docRAG_v3` repository for GraphRAG development without rewriting the whole project.

## Agent Tasks

### Task 1.1: Analyze Existing Repo

Inspect the current codebase and identify:

- PDF upload flow
- OCR or text extraction flow
- Chunking pipeline
- Embedding pipeline
- Qdrant integration
- FastAPI routes
- Celery workers
- Frontend upload and query flow

## Output

Create:

```text
docs/repo_audit.md
```

Include:

- Existing pipeline diagram
- Reusable modules
- Modules that need modification
- New modules to add

## Acceptance Criteria

- The current document ingestion path is clearly documented.
- The agent identifies where the graph pipeline can be inserted.

---

# Phase 2: Define Graph Ontology

## Objective

Create a strict ontology for research-paper graphs.

## Agent Tasks

### Task 2.1: Create Ontology File

Create:

```text
app/graph/ontology.py
```

Define allowed node types:

```text
Paper
Method
Dataset
Task
Metric
Author
Institution
Claim
Experiment
Section
```

Define allowed edge types:

```text
CITES
INTRODUCES
USES_DATASET
SOLVES_TASK
EVALUATED_ON
IMPROVES_UPON
WRITTEN_BY
AFFILIATED_WITH
HAS_SECTION
MENTIONS
REPORTS
```

### Task 2.2: Add Validation

Create validation functions that reject unknown node types and edge types.

## Output

```text
app/graph/ontology.py
tests/test_ontology.py
```

## Acceptance Criteria

- Invalid node types are rejected.
- Invalid edge types are rejected.
- Ontology is centralized in one file.

---

# Phase 3: Paper Parsing Layer

## Objective

Extract clean text and structured sections from uploaded PDFs.

## Agent Tasks

### Task 3.1: Build Paper Parser

Create:

```text
app/paper/parser.py
```

The parser should output:

```json
{
  "title": "...",
  "abstract": "...",
  "sections": [
    {
      "heading": "Introduction",
      "text": "..."
    }
  ],
  "references": []
}
```

### Task 3.2: Add Fallback Parsing

Use existing OCR only when native text extraction fails.

## Output

```text
app/paper/parser.py
tests/test_parser.py
```

## Acceptance Criteria

- Parser extracts title, abstract, sections, and references.
- Parser handles at least one sample PDF.
- OCR is not used unnecessarily when text already exists.

---

# Phase 4: Citation Extraction

## Objective

Extract paper-to-paper citation relationships.

## Agent Tasks

### Task 4.1: Create Citation Extractor

Create:

```text
app/citations/extractor.py
```

Extract:

- Reference titles
- Authors
- Year
- DOI if available
- arXiv ID if available

### Task 4.2: Normalize Citations

Create:

```text
app/citations/normalizer.py
```

Normalize citations into a stable format:

```json
{
  "title": "...",
  "authors": ["..."],
  "year": 2020,
  "doi": "...",
  "arxiv_id": "..."
}
```

## Output

```text
app/citations/extractor.py
app/citations/normalizer.py
tests/test_citation_extraction.py
```

## Acceptance Criteria

- References are extracted from a sample paper.
- Citation metadata is normalized.
- Duplicate citations are merged.

---

# Phase 5: Entity Extraction

## Objective

Extract important research entities from paper text.

## Agent Tasks

### Task 5.1: Create Entity Extractor

Create:

```text
app/graph/entity_extractor.py
```

Extract entities of these types:

```text
Method
Dataset
Task
Metric
Claim
Experiment
```

### Task 5.2: Output Structured JSON

Expected output:

```json
[
  {
    "name": "Transformer",
    "type": "Method",
    "source_section": "Abstract",
    "evidence": "The Transformer is the first transduction model..."
  }
]
```

## Output

```text
app/graph/entity_extractor.py
tests/test_entity_extractor.py
```

## Acceptance Criteria

- Entity extractor returns valid ontology types only.
- Every extracted entity has evidence text.
- Empty or malformed entities are ignored.

---

# Phase 6: Relation Extraction

## Objective

Extract relationships between entities.

## Agent Tasks

### Task 6.1: Create Relation Extractor

Create:

```text
app/graph/relation_extractor.py
```

Extract relations such as:

```text
Transformer INTRODUCES Self Attention
Transformer EVALUATED_ON WMT14
Transformer IMPROVES_UPON RNN
```

### Task 6.2: Validate Relations

Each relation must include:

```json
{
  "source": "Transformer",
  "source_type": "Method",
  "relation": "EVALUATED_ON",
  "target": "WMT14",
  "target_type": "Dataset",
  "evidence": "..."
}
```

## Output

```text
app/graph/relation_extractor.py
tests/test_relation_extractor.py
```

## Acceptance Criteria

- Relations use only allowed edge types.
- Relations connect valid node types.
- Each relation includes evidence.

---

# Phase 7: Paper Graph Builder

## Objective

Convert extracted entities and relations into one internal graph per paper.

## Agent Tasks

### Task 7.1: Create Graph Builder

Create:

```text
app/graph/paper_graph_builder.py
```

Input:

```json
{
  "paper_metadata": {},
  "sections": [],
  "entities": [],
  "relations": [],
  "citations": []
}
```

Output:

```json
{
  "nodes": [],
  "edges": []
}
```

### Task 7.2: Add Deduplication

Deduplicate nodes by:

- Normalized name
- Type
- Paper ID

## Output

```text
app/graph/paper_graph_builder.py
tests/test_paper_graph_builder.py
```

## Acceptance Criteria

- One paper produces one graph.
- Duplicate entities are merged.
- Paper node connects to sections, entities, authors, and citations.

---

# Phase 8: Neo4j Integration

## Objective

Store paper graphs and citation graphs in Neo4j.

## Agent Tasks

### Task 8.1: Create Neo4j Client

Create:

```text
app/storage/neo4j_client.py
```

Support:

- Create node
- Create edge
- Merge node
- Merge edge
- Query by paper ID
- Query by entity name

### Task 8.2: Store Paper Graph

Create:

```text
app/storage/graph_repository.py
```

## Output

```text
app/storage/neo4j_client.py
app/storage/graph_repository.py
tests/test_neo4j_client.py
```

## Acceptance Criteria

- Nodes and edges are stored in Neo4j.
- Re-ingesting the same paper does not create duplicates.
- Citation edges connect paper nodes.

---

# Phase 9: Vector Indexing

## Objective

Keep vector search as a supporting retrieval layer.

## Agent Tasks

### Task 9.1: Create Embedding Indexer

Create:

```text
app/vector/indexer.py
```

Index:

- Paper summaries
- Abstracts
- Sections
- Claims
- Entity descriptions

### Task 9.2: Store Metadata

Each vector entry should include:

```json
{
  "paper_id": "...",
  "section": "...",
  "node_type": "...",
  "node_name": "...",
  "source_text": "..."
}
```

## Output

```text
app/vector/indexer.py
tests/test_vector_indexer.py
```

## Acceptance Criteria

- Embeddings are stored in Qdrant.
- Each embedding has traceable metadata.
- Vector entries link back to paper graph nodes where possible.

---

# Phase 10: Full Ingestion Pipeline

## Objective

Connect parsing, extraction, graph building, Neo4j storage, and Qdrant indexing.

## Agent Tasks

### Task 10.1: Create Pipeline Orchestrator

Create:

```text
app/pipeline/paper_ingestion_pipeline.py
```

Pipeline:

```text
PDF
→ Parse paper
→ Extract citations
→ Extract entities
→ Extract relations
→ Build paper graph
→ Store graph in Neo4j
→ Store embeddings in Qdrant
```

### Task 10.2: Connect to Celery Worker

Modify the existing document ingestion worker so it can call the new graph pipeline.

## Output

```text
app/pipeline/paper_ingestion_pipeline.py
tests/test_paper_ingestion_pipeline.py
```

## Acceptance Criteria

- Uploading one paper triggers the full pipeline.
- Neo4j receives graph data.
- Qdrant receives vector data.
- Pipeline status is visible through the existing backend.

---

# Phase 11: Graph Retrieval

## Objective

Retrieve information using graph traversal.

## Agent Tasks

### Task 11.1: Create Graph Retriever

Create:

```text
app/retrieval/graph_retriever.py
```

Support queries like:

- Find papers that cite a paper
- Find papers that use a dataset
- Find methods evaluated on a dataset
- Find methods that improve upon another method
- Find all entities related to a paper

## Output

```text
app/retrieval/graph_retriever.py
tests/test_graph_retriever.py
```

## Acceptance Criteria

- Graph retriever returns nodes and relationships.
- Results include evidence where available.
- Results include source paper IDs.

---

# Phase 12: Vector Retrieval

## Objective

Retrieve semantic context from Qdrant.

## Agent Tasks

### Task 12.1: Create Vector Retriever

Create:

```text
app/retrieval/vector_retriever.py
```

Support:

- Query embedding
- Top-k retrieval
- Metadata filtering by paper ID
- Metadata filtering by node type

## Output

```text
app/retrieval/vector_retriever.py
tests/test_vector_retriever.py
```

## Acceptance Criteria

- Vector retriever returns relevant text chunks.
- Results include paper metadata.
- Results can be filtered.

---

# Phase 13: Hybrid Retrieval Router

## Objective

Route user queries to graph search, vector search, or both.

## Agent Tasks

### Task 13.1: Create Query Classifier

Create:

```text
app/retrieval/query_classifier.py
```

Classify query into:

```text
EXPLANATION
COMPARISON
EVOLUTION
CITATION
SURVEY
ENTITY_LOOKUP
```

### Task 13.2: Create Hybrid Retriever

Create:

```text
app/retrieval/hybrid_retriever.py
```

Retrieval logic:

```text
EXPLANATION → Vector Retrieval
CITATION → Graph Retrieval
EVOLUTION → Citation Expansion + Graph Retrieval
COMPARISON → Graph Retrieval + Vector Retrieval
SURVEY → Graph Retrieval + Vector Retrieval
ENTITY_LOOKUP → Graph Retrieval
```

## Output

```text
app/retrieval/query_classifier.py
app/retrieval/hybrid_retriever.py
tests/test_hybrid_retriever.py
```

## Acceptance Criteria

- Query is routed correctly.
- Hybrid retriever combines graph and vector results.
- Output is formatted for LLM context construction.

---

# Phase 14: Citation Expansion Engine

## Objective

Expand retrieval across citation links.

## Agent Tasks

### Task 14.1: Create Citation Expansion Module

Create:

```text
app/retrieval/citation_expander.py
```

Support:

- Forward citation expansion
- Backward citation expansion
- Depth limit
- Max paper limit

Example:

```text
Paper A
→ cited papers
→ papers cited by those papers
```

## Output

```text
app/retrieval/citation_expander.py
tests/test_citation_expander.py
```

## Acceptance Criteria

- Citation expansion respects depth limits.
- Expansion does not loop infinitely.
- Results include citation path.

---

# Phase 15: LLM Answer Generation

## Objective

Generate answers using retrieved graph and vector context.

## Agent Tasks

### Task 15.1: Create Context Builder

Create:

```text
app/llm/context_builder.py
```

Context should include:

- Retrieved graph facts
- Retrieved text evidence
- Citation paths
- Source paper metadata

### Task 15.2: Create Answer Generator

Create:

```text
app/llm/answer_generator.py
```

Output format:

```json
{
  "answer": "...",
  "sources": [],
  "graph_facts_used": [],
  "confidence_notes": []
}
```

## Output

```text
app/llm/context_builder.py
app/llm/answer_generator.py
tests/test_answer_generator.py
```

## Acceptance Criteria

- Answers include sources.
- Answers are grounded in retrieved context.
- The model does not invent citations outside the retrieved sources.

---

# Phase 16: API Integration

## Objective

Expose GraphRAG functionality through FastAPI.

## Agent Tasks

### Task 16.1: Add Graph Query Endpoint

Create or modify:

```text
app/api/routes/graph_query.py
```

Endpoint:

```text
POST /graph-query
```

Request:

```json
{
  "query": "How did attention mechanisms evolve?",
  "project_id": "...",
  "top_k": 10
}
```

Response:

```json
{
  "answer": "...",
  "sources": [],
  "retrieval_trace": {}
}
```

### Task 16.2: Add Paper Graph Endpoint

Endpoint:

```text
GET /papers/{paper_id}/graph
```

## Output

```text
app/api/routes/graph_query.py
tests/test_graph_query_api.py
```

## Acceptance Criteria

- User can query the graph system through API.
- User can fetch a paper graph.
- API returns clear errors for missing papers or empty projects.

---

# Phase 17: Frontend Integration

## Objective

Update frontend to support graph-based paper exploration.

## Agent Tasks

### Task 17.1: Add Graph Query Page

Add a query page that sends requests to:

```text
POST /graph-query
```

### Task 17.2: Show Sources

Display:

- Answer
- Source papers
- Graph facts used
- Citation path if available

### Task 17.3: Basic Graph View

Display a simple paper graph view.

Do not build complex graph animations at this stage.

## Output

Frontend changes in the existing frontend app.

## Acceptance Criteria

- User can upload papers.
- User can ask graph-based questions.
- User can see sources and graph facts.

---

# Phase 18: Evaluation Dataset

## Objective

Create a small benchmark for testing retrieval and answer quality.

## Agent Tasks

### Task 18.1: Create Evaluation Questions

Create:

```text
evaluation/questions.json
```

Include at least 50 questions across:

```text
Explanation
Comparison
Citation
Evolution
Survey
Entity lookup
```

Example:

```json
{
  "question": "Which papers improved upon Transformer?",
  "expected_entities": ["Transformer"],
  "expected_relation": "IMPROVES_UPON",
  "expected_sources": []
}
```

### Task 18.2: Create Evaluation Runner

Create:

```text
evaluation/run_eval.py
```

Measure:

- Retrieval hit rate
- Source correctness
- Citation correctness
- Answer grounding

## Output

```text
evaluation/questions.json
evaluation/run_eval.py
```

## Acceptance Criteria

- Evaluation can run from command line.
- Results are saved to JSON.
- Evaluation compares graph retrieval vs vector retrieval vs hybrid retrieval.

---

# Phase 19: Documentation

## Objective

Document the full system.

## Agent Tasks

### Task 19.1: Create Architecture Docs

Create:

```text
docs/architecture.md
```

Include:

- System architecture
- Data flow
- Storage design
- Retrieval design

### Task 19.2: Create Developer Setup Guide

Create:

```text
docs/setup.md
```

Include:

- Local setup
- Environment variables
- Running Neo4j
- Running Qdrant
- Running backend
- Running frontend
- Running tests

### Task 19.3: Create Decisions Log

Create:

```text
docs/decisions.md
```

Track:

- Why Neo4j is used
- Why Qdrant is kept
- Why graph pipeline is added beside existing RAG
- Why ontology is restricted

## Output

```text
docs/architecture.md
docs/setup.md
docs/decisions.md
```

## Acceptance Criteria

- New developer can understand the project.
- New developer can run the system locally.
- Major architecture decisions are recorded.

---

# Recommended Agent Execution Order

Use this order:

```text
Phase 1  → Repo Audit
Phase 2  → Ontology
Phase 3  → Paper Parser
Phase 4  → Citation Extraction
Phase 5  → Entity Extraction
Phase 6  → Relation Extraction
Phase 7  → Paper Graph Builder
Phase 8  → Neo4j Integration
Phase 9  → Vector Indexing
Phase 10 → Full Ingestion Pipeline
Phase 11 → Graph Retrieval
Phase 12 → Vector Retrieval
Phase 13 → Hybrid Retrieval
Phase 14 → Citation Expansion
Phase 15 → LLM Answer Generation
Phase 16 → API Integration
Phase 17 → Frontend Integration
Phase 18 → Evaluation
Phase 19 → Documentation
```

---

# First Milestone

The first real milestone is:

```text
Upload one research paper
→ Parse it
→ Extract entities
→ Extract relations
→ Build graph
→ Store graph in Neo4j
→ View graph through API
```

Do not move to advanced retrieval until this works.

---

# Second Milestone

The second milestone is:

```text
Upload 10 related papers
→ Build paper graphs
→ Build citation graph
→ Ask graph queries
```

Example query:

```text
Which methods improved upon Transformer?
```

---

# Third Milestone

The third milestone is:

```text
Graph Retrieval
+
Vector Retrieval
+
LLM Answer Generation
```

Example query:

```text
How did attention mechanisms evolve from RNNs to Transformers?
```

The answer should include:

- Explanation
- Source papers
- Graph facts used
- Citation path

---

# Implementation Notes

## Keep the Existing Repo

Do not start from scratch. Keep the current `docRAG_v3` system as the base platform.

Reuse:

- FastAPI backend
- Celery workers
- Redis queue
- PDF upload flow
- Existing extraction flow where useful
- Qdrant integration
- Frontend shell

Add:

- Graph ontology
- Paper parser improvements
- Citation extraction
- Entity extraction
- Relation extraction
- Neo4j storage
- Graph retrieval
- Hybrid retrieval
- Evaluation suite

## Core Principle

The main technical risk is not the UI, LLM, or orchestration framework.

The main technical risk is:

```text
Paper
→ Entity Extraction
→ Relation Extraction
→ Graph Construction
```

Solve this first before building advanced agents or complex workflows.
