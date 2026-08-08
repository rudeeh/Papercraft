# Local Verification Runbook

Run this on a machine with **Docker Desktop running** and a checkout of
`main`. It brings the full stack up, ingests a PDF, queries the graph,
exercises the curation loop, and runs the eval harness.

> **Why local?** The cloud workspace's network policy blocks Docker
> registries and Neo4j's download hosts, so the full stack can't run
> there. The 577-test unit suite runs anywhere and needs none of these
> services.

> **What changed since the first draft of this runbook:** it used to start
> by cloning `docRAG_v3` — that is no longer the baseline, `main` is. And
> step 2 used to fail outright: `requirements.txt` pinned
> `weaviate-client==4.22.0` against `pydantic==2.6.1`, which pip cannot
> resolve, so `--build` died before anything started. That is fixed.

---

## 1. Configure

```bash
cd Papercraft
cp .env.example .env
```

The defaults work. The one worth setting is an OpenRouter key, needed for
`/graph-query` answer generation (the default model
`openai/gpt-oss-20b:free` is free-tier):

```
OPENROUTER_API_KEY=sk-or-v1-...your real key...
```

Optional, to exercise the curation queue in step 6 — otherwise the
deterministic extractor's output all clears the auto-insert threshold and
the queue is legitimately empty:

```
EXTRACTION_PROVIDER=hybrid
```

## 2. Bring up the stack

```bash
docker compose up -d --build
```

The first build is slow — torch and doctr are large. Then:

```bash
docker compose ps
```

**✅ Check 1:** `redis`, `weaviate`, `neo4j`, `postgres`, `minio`, `api`,
`worker`, `frontend` and `web` are all `Up`; `neo4j` and `postgres` show
`healthy`.

```bash
curl http://localhost:8000/api/v1/health
```

**✅ Check 2:** returns `{"status":"ok"}`. (The `api` container waits on
Neo4j's healthcheck, so allow ~30s after the build.)

## 3. Ingest a paper

```bash
./scripts/load_small_pdf.sh
# or a real cs.CV paper:
./scripts/load_small_pdf.sh path/to/some-paper.pdf
```

**✅ Check 3:** ends with `Processing Complete!` (status `SUCCESS`).

Note the `task_id`, then:

```bash
curl -s http://localhost:8000/api/v1/status/<task_id> | python -m json.tool
```

**✅ Check 4:** `pipeline_steps` lists all 14 steps. `OCR` and `PARSING`
must be `success`. `ENRICHMENT` is `success` if OpenAlex recognised the
paper and `error`/`skipped` otherwise — either is fine, enrichment is
additive. `NEO4J_STORE` and `VECTOR_STORE` should be `success`; `skipped`
there means the datastore wasn't reachable during ingestion.

## 4. Verify the graph populated

Neo4j browser at **http://localhost:7474** (`neo4j` / `password`):

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC;
```

**✅ Check 5:** `Paper` plus extraction labels (`Method`, `Dataset`,
`Task`, `Metric`, `Author`, `Section`, ... — the exact mix depends on the
paper).

## 5. Graph-query

```bash
curl -s -X POST http://localhost:8000/api/v1/graph-query \
  -H "Content-Type: application/json" \
  -d '{"query": "What methods does this paper use?"}' | python -m json.tool
```

**✅ Check 6:** a grounded answer plus a `retrieval_trace` (query type,
graph facts, vector results). A `401` means no API key — recheck step 1
and `docker compose restart api worker`. A `503` means Neo4j or Weaviate
is unreachable from the API container.

## 6. Curation loop

```bash
curl -s http://localhost:8000/api/v1/curation/stats | python -m json.tool
curl -s "http://localhost:8000/api/v1/curation/drafts?limit=5" | python -m json.tool
```

**✅ Check 7:** `stats` returns counts (a `503` here means PostgreSQL is
unreachable). With `EXTRACTION_PROVIDER=hybrid` and an API key there
should be pending drafts; on the default `heuristic` provider an empty
queue is the correct answer, not a failure.

If there is a draft, promote it and confirm it reaches the graph:

```bash
curl -s -X POST http://localhost:8000/api/v1/curation/drafts/<draft_id>/promote \
  -H "Content-Type: application/json" -d '{}' | python -m json.tool
```

**✅ Check 8:** `"status": "promoted"`. In Neo4j, the promoted node now
carries `curated: true`:

```cypher
MATCH (n {curated: true}) RETURN labels(n)[0] AS label, n.name AS name;
```

Or use the UI at **http://localhost:3000/curate**.

## 7. Auth

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","display_name":"You","password":"a-good-long-password"}' \
  | python -m json.tool
```

**✅ Check 9:** `201` with an `access_token`. `GET /api/v1/auth/me` with
`Authorization: Bearer <token>` returns that user; without the header it
returns the anonymous curator (single-player mode).

## 8. Eval harness

```bash
docker compose exec api python -m evaluation.run_eval --limit 10
```

**✅ Check 10:** runs without errors and writes `evaluation/results.json`
comparing graph / vector / hybrid modes. Scores will be low with only one
or two papers ingested — this check is that it *runs*.

## 9. Unit tests

These need no services at all:

```bash
docker compose exec api pytest -q
```

**✅ Check 11:** `577 passed`.

---

## Report back

1. `docker compose ps`
2. The label/count table from Check 5
3. The `/graph-query` response (`answer` + `retrieval_trace`)
4. Curation stats, and whether a promotion reached the graph
5. Whether `run_eval` completed, and the report filename
6. `pytest` summary line
7. Anything that failed, with the error text
