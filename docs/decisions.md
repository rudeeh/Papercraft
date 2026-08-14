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

---

## ADR: Weaviate replaces Qdrant as the vector store (Papercraft)

**Decision (2026-08-07):** Swap the vector store from Qdrant to Weaviate
1.28, per the Papercraft spec and the clone-and-adapt plan's vector-DB
decision (Option B, chosen by the project owners). `WeaviateClientWrapper`
(`app/storage/weaviate_client.py`) replaces `QdrantClientWrapper` with the
same public surface; `VectorRepository`, the legacy `/chat` vector path,
worker, API, and eval harness all swapped in the same change.

**What was deliberately preserved:**
- The payload schema (`paper_id`, `text`, `chunk_id`, `section`, `page`,
  `chunk_index`, `node_type`, `node_name`, `source_text`) — now declared
  as an explicit Weaviate property schema.
- Deterministic uuid5 chunk IDs (same namespace), so re-ingestion stays
  idempotent via Weaviate's PUT batch semantics.
- Score semantics: `search()` returns similarity (`1 - distance`), so
  cosine scores are comparable to the old Qdrant scores.
- Dimension-mismatch recreate-on-model-swap: Weaviate doesn't record a
  dim for self-provided vectors, so it's stored in the collection
  description (`dim=384`) and validated by `ensure_collection()`.

**Notes:** Weaviate class names must match `[A-Z][_0-9A-Za-z]*`, so
config collection names are normalized (`documents` -> `Documents`).
The v4 python client needs the gRPC port (50051) exposed alongside HTTP.
In docker-compose the Weaviate HTTP port maps to host 8082 because the
Angular frontend still holds 8080; in-network services use
`http://weaviate:8080` regardless.

**Verified:** full unit suite (326 passing, Weaviate client + repository
mocked tests included) plus a live smoke test against a real Weaviate
1.28.4 (store, filtered search, hybrid graph-filter search, paper
aggregation, idempotent re-ingestion, dim-recreate).

---

## ADR: PostgreSQL is an *optional* datastore, like Neo4j and Weaviate

**Decision (2026-08-09):** Add PostgreSQL for users, ingestion jobs,
extraction drafts, attestations and the audit log — but build the engine
lazily and let `init_db()` return `False` rather than raise when it is
unreachable.

**Why:** Every other datastore in this codebase already degrades rather
than fails: the worker skips graph steps when Neo4j is down and vector
steps when Weaviate is down, and the ingestion path still completes.
Making the relational store the one hard dependency would mean a Postgres
restart takes down `/upload` and `/chat`, which have nothing to do with
it. Instead only the auth and curation routes 503.

**Cost:** Two different "database is down" behaviours to keep in mind, and
`get_session` has to translate a driver error into a 503 at the route
boundary rather than letting it become a 500.

---

## ADR: `create_all` rather than Alembic

**Decision (2026-08-09):** Manage the relational schema with
`Base.metadata.create_all` at startup. No migration tool.

**Why:** These tables are append-mostly bookkeeping with no production
data to migrate. Alembic's value is a reviewed, reversible history of
schema change against data you cannot lose; buying that before there is
any such data is ceremony. The models are also deliberately written in the
portable SQLAlchemy subset (String PKs, `JSON` not `JSONB`, no
server-side defaults) so the test suite can run them on in-memory SQLite,
and drift into PostgreSQL-only territory fails a test rather than a
deploy.

**When this must change:** the first time a column is dropped or retyped
against a database anyone cares about. `create_all` never alters an
existing table, so at that point it silently does nothing and the app
breaks against a stale schema.

---

## ADR: PBKDF2 from the standard library, not bcrypt or argon2

**Decision (2026-08-09):** Hash passwords with PBKDF2-HMAC-SHA256 at
390,000 iterations (`app/core/security.py`), and sign access tokens with
a hand-rolled HS256 JWT rather than adding PyJWT.

**Why:** bcrypt and argon2 are stronger per unit of CPU, and in a
green-field service one of them would be the default answer. Here they
arrive as C extensions, and this project's slim `python:3.11` image has a
history of exactly that class of build failure — the passlib 1.7.4 +
bcrypt 4.x pairing is a widely-hit breakage in its own right. PBKDF2 is an
accepted choice (NIST SP 800-63B), has zero install surface, and the
stored format `pbkdf2_sha256$<iterations>$<salt>$<hash>` carries its own
parameters, so raising the cost later is a lazy per-user rehash on next
login (`needs_rehash`), not a migration.

**On the hand-rolled JWT:** the entire surface needed is sign and verify
over a compact claims dict. The one thing that must not be got wrong is
accepting whatever `alg` the token asks for — the classic `"alg": "none"`
forgery — so the algorithm is pinned before the signature is checked, the
comparison is constant-time, and expiry is enforced. All three have tests.

**Cost:** Two pieces of security-relevant code we own instead of
delegate. If the auth surface grows (refresh tokens, asymmetric keys,
OAuth2 flows against ORCID) this should be replaced with a library rather
than extended.

---

## ADR: Auth is optional by default (`AUTH_REQUIRED=false`)

**Decision (2026-08-09):** Unauthenticated callers of the curation routes
resolve to a single shared anonymous curator instead of being rejected.
Setting `AUTH_REQUIRED=true` demands a bearer token everywhere, with no
code change.

**Why:** README design principle 4 is "Single-Player Mode First" — the
graph has to be useful to one researcher building a literature review
before it is useful as a network. Forcing a signup before that person can
attest their own extraction is friction against the project's own stated
sequencing.

**The one thing that does not fall back:** a *bad* token is a 401 even in
single-player mode. Only a *missing* credential falls back — a wrong one
is an error, and silently downgrading it to anonymous would hide a broken
client.

**Cost:** Attestations attributed to "anonymous" carry no reputation
signal and cannot be told apart from each other, so the unique
(draft, user) constraint means anonymous mode is effectively one vote per
draft. That is the correct conservative behaviour, but it does mean the
promote threshold is unreachable anonymously without explicit promotion.

---

## ADR: A title match is labelled, not trusted

**Decision (2026-08-09):** `OpenAlexClient.enrich()` returns
`match_method` (`doi` / `arxiv` / `title`) and `match_confidence`
alongside the metadata.

**Why:** Resolution falls back to fuzzy title search when no identifier
was extracted, and fuzzy title matching has a failure mode no threshold
fixes: character similarity cannot see negation. "Attention Is All You
Need" and "Attention Is Not All You Need" score 0.93, and the gap only
narrows as titles get longer, so any threshold loose enough to tolerate
OCR damage will also accept the wrong paper. Rather than pretend a
threshold solves it, the uncertainty is reported and there is a test
(`test_negation_is_a_documented_blind_spot`) pinning the limitation open
so it cannot be quietly forgotten.

**Cost:** Consumers have to decide what to do with a weak match. Today the
pipeline treats all matches alike; the field exists so that can change
without another round of plumbing.

---

## ADR: Confidence routing withholds nothing it cannot queue

**Decision (2026-08-09):** When the ingestion pipeline has no draft sink
(no PostgreSQL), or the queue write fails, review-needed extractions are
written into the graph instead of being held back.

**Why:** The point of routing is to keep uncertain extractions out of the
canonical graph *until a human looks at them*. That trade only makes sense
if there is somewhere for them to wait. With no queue, "withhold" does not
mean "defer", it means "delete" — the extraction is gone and no one will
ever review it. Inserting it is the lesser harm and is exactly the
pre-routing behaviour, so a database outage costs review, never data. Both
paths are tested.

**Related:** heuristic extractions score 0.90, above the 0.85 auto-insert
threshold, so a heuristic-only deployment's graph is unaffected by the
router's introduction. A deterministic regex match against a curated term
list is reproducible and a reviewer can read the rule that fired — a
different kind of claim from a model's self-reported score.

---

## ADR: Promotion does not block on Neo4j

**Decision (2026-08-09):** `curation.promote_draft()` marks a draft
promoted even when the graph write fails, and records
`graph_written: false` in the audit log.
`curation.pending_graph_writes()` derives the replay set from those rows.

**Why:** A curator's review is scarce and irreplaceable; a graph write is
cheap and repeatable. Refusing the promotion because Neo4j happened to be
restarting throws away the expensive half to protect the cheap half.
Deriving the replay set from the audit log rather than a `needs_write`
flag means the thing driving the retry is the record of what actually
happened, not a second piece of state that can disagree with it.

**Cost:** A promoted draft is briefly true in PostgreSQL and not yet true
in Neo4j. Nothing currently runs the replay automatically — it is a
function, not a scheduled job.

---

## ADR: Two frontends, for now

**Decision (2026-08-09):** Add `web/` (Next.js 14, the spec's stated
frontend) without removing `frontend-angular/`. Both are built and served
by docker-compose, on 3000 and 8080.

**Why:** The Angular app is the mature UI — upload, ask, citation
explorer, dark mode — and works. The Next.js app is three pages old. The
spec calls for Next.js, so the migration should happen, but breaking a
working UI to start one is a regression dressed as progress.

**Cost:** Two clients against one API, and a period where a feature has to
be decided into one or the other. This should end with the Angular app
being deleted once `web/` covers what it covers — not with both being
maintained indefinitely.

---

## ADR: Semantic Scholar for citation enrichment (Tier 1b)

**Decision (2026-08-14):** Add `app/services/semantic_scholar.py` to
enrich the papers *cited by* the uploaded paper, distinct from OpenAlex
which enriches the uploaded paper itself.

**Why S2 and not just OpenAlex for cited papers too:**

1. **`influentialCitationCount`** — S2 classifies each citation as
   influential or not using an ML model that examines the citation
   context. OpenAlex has no equivalent. For a researcher trying to
   identify the most important citations in a 300-reference survey,
   influence scores are dramatically more useful than raw citation
   counts.

2. **`tldr`** — S2's auto-generated TLDR (model `tldr@v2.0.0`) gives a
   1-2 sentence summary of each cited paper. OpenAlex doesn't have this.
   The TLDR is stored with provenance (`{"text": ..., "model": ...,
   "source": "semantic_scholar"}`) so any UI can label it as
   auto-generated, not ground truth.

3. **Batch endpoint** — S2's `POST /graph/v1/paper/batch` accepts up
   to 500 IDs per call. OpenAlex has no batch endpoint; each cited
   paper would need a separate request. For a 100-reference paper
   that's 1 S2 call vs. 100 OpenAlex calls.

4. **Coverage** — S2's corpus (200M+ papers) is broader than
   OpenAlex's for CS/ML papers, which is the primary use case.

**Why not S2 for the uploaded paper too?** OpenAlex is already
integrated, tested, and produces higher-fidelity metadata for the
uploaded paper (affiliations, referenced works, open access status).
S2 is additive, not a replacement.

**Why not title-based lookup for cited papers?** The regex citation
extractor's titles are too noisy (OCR damage, truncation, "et al."
artifacts) to use as S2 lookup keys. Title search would have a high
false-positive rate. We only look up cited papers by DOI or arXiv ID —
if the extractor didn't find either, the stub stays sparse. This is a
known limitation that will improve when the extractor moves to LLM-based
extraction (planned).

**Rate limiting reality:** S2's free tier (no API key) is much more
restrictive than their docs imply — roughly 1-2 requests per hour per
IP, not "1 req/s." S2 does not send `Retry-After` headers on 429, so
the client uses blind exponential backoff (1s, 2s, 4s) and gives up
after 3 attempts. For any real use, `SEMANTIC_SCHOLAR_API_KEY` is
effectively required (free signup at
https://www.semanticscholar.org/product/api#api-key-form). Without a
key, the CITATION_ENRICHMENT step silently degrades to sparse stubs
after the first batch call hits 429.

**Cost:** One additional non-critical pipeline step. If S2 is
unavailable, stubs are created with just what the regex extractor
parsed — the pipeline still completes. Latency is unmeasured due to
free-tier rate limiting; expected to be under 10s for a 100-paper
batch based on S2's documented characteristics.
