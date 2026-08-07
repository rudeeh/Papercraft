# Phase A — Local Verification Runbook

Run this on **your machine** (needs Docker Desktop + git). It executes the plan's Phase A end-to-end: bring up DocRAG v3 unmodified, ingest a test PDF, query the graph, and run the eval harness. Report the results back in the Cowork session and we tick Phase A off.

> Why local? The cloud workspace's network policy blocks Docker registries and Neo4j's download hosts, so the full stack can't run there. Everything else (315/315 unit tests, Redis, Qdrant) has already been verified in the sandbox.

---

## 1. Clone and configure

```bash
git clone https://github.com/AnshulPatil2005/docRAG_v3 papercraft-baseline
cd papercraft-baseline
cp .env.example .env
```

Edit `.env` and set your OpenRouter key (needed for `/graph-query` answer generation; the default model `openai/gpt-oss-20b:free` is free-tier):

```
OPENROUTER_API_KEY=sk-or-v1-...your real key...
```

Leave everything else at defaults.

## 2. Bring up the stack

```bash
docker compose up -d --build
```

First build takes a while (torch + doctr are heavy). Then:

```bash
docker compose ps
```

**✅ Check 1:** all services (`redis`, `qdrant`, `neo4j`, `api`, `worker`, `frontend`) are `Up`, and `neo4j` shows `healthy`.

```bash
curl http://localhost:8000/api/v1/health
```

**✅ Check 2:** returns `{"status":"ok"}` (or similar).

## 3. Ingest a test PDF

Use the bundled helper (uploads `tests/data/small.pdf` and polls until done):

```bash
./scripts/load_small_pdf.sh
```

Or upload a real cs.CV paper you have lying around:

```bash
./scripts/load_small_pdf.sh path/to/some-paper.pdf
```

**✅ Check 3:** script ends with `Processing Complete!` (status `SUCCESS`).

## 4. Verify the graph populated

Open the Neo4j browser at **http://localhost:7474** (user `neo4j`, password `password`) and run:

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count ORDER BY count DESC;
```

**✅ Check 4:** you see `Paper` plus extraction labels (`Method`, `Dataset`, `Task`, `Metric`, `Author`, ... — exact mix depends on the paper).

## 5. Graph-query

```bash
curl -s -X POST http://localhost:8000/api/v1/graph-query \
  -H "Content-Type: application/json" \
  -d '{"query": "What methods does this paper use?"}' | python3 -m json.tool
```

**✅ Check 5:** a grounded answer with a retrieval trace (mode used, graph facts / vector chunks). If you get a "no API key" error, re-check step 1 and `docker compose restart api worker`.

## 6. Eval harness

```bash
docker compose exec api python -m evaluation.run_eval
```

**✅ Check 6:** runs without errors and writes a JSON report comparing graph / vector / hybrid modes. (Scores will be low with only 1–2 papers ingested — that's fine, we only need it to *run*.)

---

## Report back

Paste into the Cowork session:

1. Output of `docker compose ps`
2. The label/count table from Check 4
3. The `/graph-query` JSON response (or its `answer` + `retrieval` fields)
4. Whether `run_eval` completed + the report filename
5. Anything that failed, with the error text

That's Phase A done. Meanwhile the sandbox side is already moving on the Weaviate swap and Phase B scaffolding — nothing blocks on you except these checks.
