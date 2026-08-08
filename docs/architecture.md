# Architecture

The system as it stands: the original vector-RAG platform (kept intact),
the GraphRAG layer built on top of it, and the Papercraft layer on top of
that -- OpenAlex enrichment, LLM extraction with confidence routing, and
the human curation loop. For the module-by-module audit of what existed
before GraphRAG work started, see [`repo_audit.md`](./repo_audit.md).

## 1. System overview

Two retrieval paths exist side by side:

- **Legacy vector-RAG** (`POST /api/v1/chat`) -- unchanged. Chunks a PDF by
  words, embeds with `sentence-transformers`, stores in Weaviate under the
  `Documents` collection, and answers by stuffing the top-k chunks into an
  LLM prompt. No citations, no entities, no graph.
- **GraphRAG** (`POST /api/v1/graph-query`) -- everything built in Phases
  2-18. Every ingested paper gets a knowledge-graph representation in
  Neo4j (methods, datasets, tasks, metrics, claims, experiments, citations)
  *and* graph-node-linked vector embeddings in Weaviate, and queries are
  routed to graph traversal, vector search, or both depending on what
  they're asking.
- **Curation** (`/api/v1/curation/*`) -- extractions the confidence router
  would not wave through wait in a PostgreSQL-backed queue for human
  attestation, and only reach Neo4j once promoted.

Both paths share the same upload endpoint, Celery worker, and PDF -- the
GraphRAG pipeline runs as additional (non-critical) steps in the same
`process_pdf_task`, so a graph-extraction failure never blocks the
existing vector-RAG path from completing.

## 2. Ingestion data flow

```
PDF upload (POST /api/v1/upload)
  -> Celery task: app.worker.tasks.process_pdf_task
       -> app.pipeline.paper_ingestion_pipeline.PaperIngestionPipeline.process()

           1. OCR              app/services/ocr.py            [CRITICAL]
              -- native PyMuPDF text extraction first; Doctr OCR only
                 runs on pages with little/no extractable text.
           2. Parsing          app/paper/parser.py             [CRITICAL]
              -- title, abstract, sections, references
           3. Citations        app/citations/{extractor,normalizer}.py
              -- raw reference + in-text mention extraction, then
                 DOI/arXiv-based dedup and merge
           4. Entities         app/graph/entity_extractor.py
              -- Method/Dataset/Task/Metric/Claim/Experiment via
                 deterministic term + regex matching (no heavy NLP dep)
           5. Relations        app/graph/relation_extractor.py
              -- co-occurrence + linguistic pattern matching, mapped to
                 ontology edge types
           6. Graph build      app/graph/paper_graph_builder.py
              -- assembles Node/Edge objects, dedups, creates citation
                 stub Paper nodes (resolved later when the cited paper
                 is itself ingested)
           7. Neo4j store      app/storage/{neo4j_client,graph_repository}.py
              -- MERGE-based writes (idempotent re-ingestion)
           8. Vector chunks    built from parsed abstract + sections +
                                entities (NOT raw OCR text -- each chunk
                                carries node_type/node_name/source_text)
           9. Embedding        app/embeddings/embedder.py
              -- local (sentence-transformers) | openai | stub backend
          10. Weaviate store   app/storage/{weaviate_client,vector_repository}.py
              -- re-ingestion-safe (deterministic uuid5 chunk IDs, so a
                 re-ingest overwrites rather than duplicates)
```

Interleaved with the above are four Papercraft steps:

```
   2b. ENRICHMENT          app/services/openalex.py
       -- resolves the paper by DOI -> arXiv ID -> fuzzy title, and
          returns canonical title/authors/year/venue/DOI plus a
          match_method saying how certain that resolution was. Runs
          before graph build so the Paper node carries it.
   5b. LLM_EXTRACTION      app/graph/llm_extractor.py
       -- two-stage (entities, then relations given those entities).
          SKIPPED unless EXTRACTION_PROVIDER is llm|hybrid. Drops any
          extraction outside the ontology or quoting evidence not
          present in the source.
   5c. CONFIDENCE_ROUTING  app/graph/confidence_router.py
       -- merges the heuristic and LLM passes, scores each item, and
          splits into auto-insert / draft / manual / discarded.
   5d. DRAFT_QUEUE         app/services/curation.py
       -- persists the review-needed items. SKIPPED when there is no
          PostgreSQL, in which case those items are inserted into the
          graph rather than dropped (see decisions.md).
```

Steps 3-10 are all non-critical: each is wrapped in try/except by
`PaperIngestionPipeline._run_step`, so a single failing step is recorded
in `PipelineResult.steps` and the pipeline continues. Neo4j, Weaviate,
PostgreSQL and OpenAlex are all optional at the pipeline level -- if a
connection isn't available (the `_create_*` helpers in
`app.worker.tasks` return `None`), the corresponding steps are marked
`SKIPPED`, not `ERROR`.

## 3. Query data flow (GraphRAG)

```
POST /api/v1/graph-query {query, project_id?, top_k?}
  app/api/graph_routes.py
    -> HybridRetriever.retrieve(query, top_k)         app/retrieval/hybrid_retriever.py
         1. QueryClassifier.classify(query)            app/retrieval/query_classifier.py
              -> EXPLANATION | COMPARISON | EVOLUTION | CITATION | SURVEY | ENTITY_LOOKUP
         2. Route per query type (see table below):
              GraphRetriever      app/retrieval/graph_retriever.py    (Neo4j)
              VectorRetriever     app/retrieval/vector_retriever.py   (Weaviate)
              CitationExpander    app/retrieval/citation_expander.py  (Neo4j, EVOLUTION only)
    -> ContextBuilder.build(retrieval_result)          app/llm/context_builder.py
         -- renders graph facts / text evidence / citation paths / source
            papers into one prompt-ready text block
    -> AnswerGenerator.generate(query, retrieval_result) app/llm/answer_generator.py
         -- prompts app/services/llm.py with a grounding-only system prompt;
            refuses to answer (without calling the LLM) if retrieval found
            nothing, rather than letting the model guess
  <- {answer, sources, retrieval_trace}
```

Query routing table (`app/retrieval/hybrid_retriever.py`):

| Query type      | Retrieval used                        |
|------------------|----------------------------------------|
| `EXPLANATION`    | Vector only                            |
| `CITATION`       | Graph only                             |
| `EVOLUTION`      | Citation expansion + Graph             |
| `COMPARISON`     | Graph + Vector                         |
| `SURVEY`         | Graph + Vector                         |
| `ENTITY_LOOKUP`  | Graph only                             |

Graph queries are anchored two ways: entities spotted in the query text
itself (reusing `EntityExtractor`'s deterministic term matching -- the
same code paper text goes through, applied to the question instead), and
an optional explicit `paper_id` when the caller already knows which paper
is in scope.

`HybridRetriever.retrieve()` also accepts `force_mode` (`"graph"` |
`"vector"` | `"both"`) to bypass the routing table entirely -- used by the
Phase 18 evaluation runner to compare all three modes on the same
question.

## 4. Storage design

### Neo4j (graph)

Ontology defined in `app/graph/ontology.py`:

- **Node types:** `Paper, Method, Dataset, Task, Metric, Author,
  Institution, Claim, Experiment, Section`
- **Edge types:** `CITES, CITED_BY, INTRODUCES, USES_METHOD, IMPROVES_UPON,
  EXTENDS, VARIANT_OF, USES_DATASET, PUBLISHED_DATASET, EVALUATES_ON,
  BENCHMARK_FOR, SOLVES_TASK, RELATED_TASK, REPORTS_METRIC, MEASURED_BY,
  WRITTEN_BY, AUTHORED_BY, AFFILIATED_WITH, HAS_SECTION, CONTAINS_CLAIM,
  MENTIONS, COMPARES_TO`
- `VALID_EDGES` maps `(source_type, edge_type) -> {allowed target types}`;
  `OntologyValidator` rejects anything outside that map, so every node and
  edge stored in Neo4j is ontology-conformant by construction.
- Every write goes through `Neo4jClient.merge_node` / `merge_edge`
  (Cypher `MERGE`, not `CREATE`), keyed by `NODE_KEY_MAP` (`paper_id` for
  Paper, `name` for Method/Dataset/Task/Metric/Author/Institution,
  `section_id`/`claim_id`/`experiment_id` for the per-paper types) --
  re-ingesting a paper never creates duplicates.
- Citations to not-yet-ingested papers become **stub Paper nodes**
  (`is_stub: true`, ID derived from DOI, then arXiv ID, then a title
  hash) so cross-paper citation edges exist immediately; when the real
  paper is later ingested, `GraphRepository.resolve_citation_stub`
  re-wires incoming `CITES` edges to it and deletes the stub.

### Weaviate (vectors)

Single collection (`WEAVIATE_COLLECTION_NAME`, default `Documents`),
managed by `WeaviateClientWrapper` + `VectorRepository`
(`app/storage/{weaviate_client,vector_repository}.py`). Each object's
payload:

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

`node_type` / `node_name` link every vector directly back to the graph
node it was derived from, which is what lets `VectorRetriever.retrieve()`
filter by node type and lets citation expansion restrict vector search to
a specific set of `paper_id`s. Chunk IDs are deterministic uuid5 values,
so re-ingesting a paper overwrites its objects rather than duplicating
them -- idempotent the same way Neo4j writes are.

### PostgreSQL (everything *about* the graph, rather than in it)

`app/db/models.py`, created at startup by `init_db()`:

| Table | Holds |
|---|---|
| `users` | Curators, their reputation, and the shared anonymous account |
| `ingestion_jobs` | A durable record of each PDF's run, surviving a Redis flush |
| `extraction_drafts` | Extractions awaiting review, payload stored verbatim so promotion needs no re-extraction |
| `attestations` | One vote per (draft, curator) -- the unique constraint is what makes the score trustworthy |
| `audit_log` | Append-only: who changed what, `actor_id` NULL meaning the pipeline itself |

The engine is built lazily and `init_db()` reports failure rather than
raising, so PostgreSQL is optional the same way Neo4j and Weaviate are.

### MinIO / S3 (PDFs)

`app/storage/object_store.py`, off by default (`OBJECT_STORE_ENABLED`).
Makes uploads durable across container rebuilds and shareable between api
and worker replicas, which a bind-mounted volume only appears to do.

## 5. Component map

| Phase | Concern | Key files |
|---|---|---|
| 2 | Ontology | `app/graph/ontology.py` |
| 3 | Paper parsing | `app/paper/parser.py` |
| 4 | Citation extraction | `app/citations/{extractor,normalizer}.py` |
| 5 | Entity extraction | `app/graph/entity_extractor.py` |
| 6 | Relation extraction | `app/graph/relation_extractor.py` |
| 7 | Paper graph builder | `app/graph/paper_graph_builder.py` |
| 8 | Neo4j storage | `app/storage/{neo4j_client,graph_repository}.py` |
| 9 | Vector indexing | `app/embeddings/embedder.py`, `app/storage/{weaviate_client,vector_repository}.py` |
| 10 | Ingestion pipeline | `app/pipeline/paper_ingestion_pipeline.py`, `app/worker/tasks.py` |
| 11 | Graph retrieval | `app/retrieval/graph_retriever.py` |
| 12 | Vector retrieval | `app/retrieval/vector_retriever.py` |
| 13 | Hybrid retrieval router | `app/retrieval/{query_classifier,hybrid_retriever}.py` |
| 14 | Citation expansion | `app/retrieval/citation_expander.py` |
| 15 | Answer generation | `app/llm/{context_builder,answer_generator}.py` |
| 16 | API integration | `app/api/graph_routes.py` |
| 17 | Frontend integration | `frontend-angular/src/app/components/ask/`, `web/app/` |
| 18 | Evaluation | `evaluation/{questions.json,run_eval.py}` |
| -- | Metadata enrichment | `app/services/openalex.py` |
| -- | LLM extraction | `app/graph/llm_extractor.py` |
| -- | Confidence routing | `app/graph/confidence_router.py` |
| -- | Curation engine | `app/services/curation.py`, `app/api/curation_routes.py` |
| -- | Auth | `app/core/security.py`, `app/api/{deps,auth_routes}.py` |
| -- | Relational storage | `app/db/{base,models}.py` |
| -- | Object storage | `app/storage/object_store.py` |

## 6. Extraction: deterministic by default, LLM by opt-in

Entity/relation extraction, query classification, and citation
mention-parsing are all **deterministic regex/keyword heuristics** by
default -- no LLM, no heavy NLP model. The graph pipeline therefore works
with no extra model dependency, stays fast, and is fully testable. It
trades off recall on entities and relations phrased in ways the patterns
don't cover.

`EXTRACTION_PROVIDER=llm|hybrid` adds the two-stage LLM pass to recover
that recall. What keeps it safe is that its output is not trusted equally:

- An extraction outside the ontology is **dropped**, never coerced into
  the nearest legal type.
- An extraction whose quoted evidence does not appear in the source is
  **dropped** -- the cheapest available hallucination check.
- What survives is *scored*, not inserted. Only extractions at or above
  `CONFIDENCE_AUTO_INSERT` enter the graph unreviewed; the rest wait for a
  human in the curation queue.
- Independent agreement between the two passes is the strongest signal
  available without a human, and is what lifts a medium-confidence model
  extraction over the auto-insert line.

Heuristic extractions score 0.90 -- above the 0.85 default -- so a
heuristic-only deployment's graph is unaffected by any of this machinery
existing. See [`decisions.md`](./decisions.md) for why.
