# Architecture Decisions Log (Phase 19.3)

Short-form ADRs for the choices that shaped the GraphRAG layer. Each entry
is decision -> why -> what it costs.

---

## Why Neo4j

**Decision:** Store the per-paper and cross-paper knowledge graph in Neo4j,
alongside the existing Qdrant vector store.

**Why:** The whole point of GraphRAG here is answering questions vector
search structurally can't: "which papers cite X", "what improved upon Y",
"how did Z evolve across papers". Those are graph-traversal queries
(`find_citing_papers`, `find_methods_improving_upon`, multi-hop citation
expansion) -- expressing them as Cypher (`MATCH (a)-[:CITES]->(b)`) is
direct; expressing them as vector similarity is not, and would require a
graph database's traversal semantics either way, just built on top of
something not designed for it. Neo4j specifically because it's the most
mature property-graph store with a mainstream Python driver, `MERGE`
semantics that make idempotent re-ingestion straightforward, and a
built-in browser (`:7474`) for inspecting the graph during development.

**Cost:** A second stateful service to run and back up, and Cypher is a
second query language beyond the Qdrant filter DSL already in use.

---

## Why Qdrant is kept (not replaced by the graph)

**Decision:** Keep Qdrant as the semantic-search layer rather than folding
everything into graph traversal.

**Why:** Graph traversal answers questions about *known* entities and
relationships; it doesn't answer "explain how self-attention works" or
anything else that needs matching free-text meaning rather than a named
entity or relation. Vector search remains the only way to surface
relevant prose the extraction pipeline didn't turn into a graph fact
(e.g. general explanatory text, code discussion in prose, cross-paper
survey material). This is why `HybridRetriever` routes `EXPLANATION`
queries to vector-only and reserves graph-only routing for the query
types that are inherently structural (`CITATION`, `ENTITY_LOOKUP`).

**Cost:** Running two databases, and the ingestion pipeline having to keep
each in sync (both are re-ingestion-safe/idempotent independently, but
there's no cross-store transaction -- a paper can in principle end up with
a graph but no vectors, or vice versa, if one store is down during
ingestion; the pipeline's per-step status tracking makes this visible
rather than silent).

---

## Why the graph pipeline sits *beside* the existing RAG path, not replacing it

**Decision:** `POST /api/v1/chat` (word-chunked, no citations, no graph)
stays exactly as it was; `POST /api/v1/graph-query` is new and additive.
The ingestion worker runs both the legacy chunk/embed/upsert path and the
new graph+embedding path in the same task.

**Why:** Per `AGENT_PHASES.md`'s core principle, the main technical risk
was proving entity extraction -> relation extraction -> graph construction
actually works, not building a second product. Keeping the existing,
working vector-RAG path untouched meant that risk could be taken on
incrementally, phase by phase, with every phase's acceptance criteria
independently testable, without ever leaving the system in a state where
*neither* path worked. It also meant existing callers of `/chat` were
never broken by GraphRAG development.

**Cost:** Some duplication -- there are now two chunking/embedding code
paths (`app/services/{text_processing,embeddings,vector_store}.py` for
legacy chat, `app/embeddings/embedder.py` +
`app/storage/vector_repository.py` for GraphRAG) writing into the *same*
Qdrant collection with different payload shapes (`doc_id`/`filename` vs
`paper_id`/`node_type`/`node_name`). They coexist safely because neither
path filters on the other's fields, but consolidating onto one embedding
path is the natural next step once the legacy `/chat` endpoint is
deprecated or migrated to the same payload schema.

---

## Why the ontology is restricted to a fixed node/edge type list

**Decision:** `app/graph/ontology.py` defines a closed set of node types
(`Paper, Method, Dataset, Task, Metric, Author, Institution, Claim,
Experiment, Section`) and edge types, with an explicit
`(source_type, edge_type) -> {valid target types}` map, and
`OntologyValidator` rejects anything outside it at construction time
(`Node.__init__` / `Edge.__init__` raise `ValueError`).

**Why:** Entity and relation extraction here are deterministic
regex/keyword heuristics (see below), not an LLM -- there's nothing
stopping a pattern from producing a slightly-off label ("Methods" vs
"Method", "IMPROVES-UPON" vs "IMPROVES_UPON") if the schema weren't
enforced. A closed, validated ontology means every node and edge that
makes it into Neo4j is guaranteed queryable by the fixed set of Cypher
patterns `GraphRepository`/`GraphRetriever` use -- there's no long tail of
one-off relationship strings to special-case in retrieval code. It also
makes the graph predictable enough for a fixed evaluation set
(`evaluation/questions.json`) to assert specific expected relations.

**Cost:** Real relationships that don't fit the ontology are silently
dropped (`PaperGraphBuilder._safe_edge` catches `ValueError` and drops the
edge rather than raising) rather than stored loosely. Extending the graph
to a new kind of fact means touching `ontology.py` first, not just the
extractor.

---

## Why entity/relation/query-classification are deterministic heuristics, not an LLM

**Decision:** `EntityExtractor`, `RelationExtractor`, and
`QueryClassifier` all use known-term lookups and regex patterns, not a
call to an LLM or a spaCy/transformers NER model.

**Why:** Keeps the ingestion pipeline's per-step cost and latency low and
fully deterministic/testable (every extractor has a unit test suite that
runs in milliseconds, no API key or GPU required), and keeps the only
place an LLM is actually called to be answer generation
(`app/llm/answer_generator.py`), where grounding it against retrieved
context matters far more than at extraction time.

**Cost:** Recall is bounded by the known-term vocabulary
(`EntityExtractor.KNOWN_METHODS/KNOWN_DATASETS/...`) and pattern coverage
-- a method name or relation phrased in a way the patterns don't
anticipate won't be extracted. `evaluation/run_eval.py`'s retrieval-hit-rate
metric is exactly the mechanism intended to surface this over time as more
papers get ingested.

---

## Why vector chunks carry `node_type` / `node_name` (Phase 9/10 rework)

**Decision:** The ingestion pipeline builds GraphRAG vector chunks from
the parsed abstract, sections, and extracted entities (each tagged with
`node_type`/`node_name`/`source_text`), rather than embedding raw
word-chunked OCR text the way the legacy `/chat` path does.

**Why:** Without this link, `VectorRetriever`'s `node_type` filter and
`CitationExpander`-driven paper-scoped vector search (Phase 12/13/14)
would have nothing to filter on -- a chunk of raw prose has no relationship
to a specific graph node. Tagging every chunk lets a query like "find
Claim-type evidence" or "search only within these cited papers" actually
work, and lets `ContextBuilder` trace every piece of text evidence back to
the paper and (when applicable) the graph node it came from.

**Cost:** More, smaller embedding calls per paper (one per section/entity
instead of one per fixed-size word chunk), and the GraphRAG vector chunks
are a different granularity than the legacy chat path's chunks living in
the same collection (see the "graph pipeline beside existing RAG" entry
above).
