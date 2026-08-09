# arXiv Ingestion Endpoint

`POST /api/v1/ingest/arxiv` — ingest a paper from arXiv by id. The
endpoint validates the id synchronously, enqueues a Celery task that
downloads the PDF and runs it through the existing
[`PaperIngestionPipeline`](../app/pipeline/paper_ingestion_pipeline.py),
and returns a task id that can be polled via
[`GET /api/v1/status/{task_id}`](./setup.md#status-endpoint).

This is the first non-upload ingestion source in Papercraft. It mirrors
the shape of `POST /api/v1/upload` (same rate limit, same idempotent
doc_id strategy, same status-polling contract) so frontends and
integrations can treat the two interchangeably.

---

## Request

```http
POST /api/v1/ingest/arxiv
Content-Type: application/json

{
  "arxiv_id": "2401.12345"
}
```

The `arxiv_id` field accepts any of the following shapes, all of which
resolve to the same canonical id:

| Input form | Example | Canonical id |
|---|---|---|
| Bare id | `2401.12345` | `2401.12345` |
| Versioned | `2401.12345v2` | `2401.12345` |
| `arXiv:` prefix | `arXiv:2401.12345v3` | `2401.12345` |
| abs URL | `https://arxiv.org/abs/2401.12345` | `2401.12345` |
| abs URL, versioned | `https://arxiv.org/abs/2401.12345v2` | `2401.12345` |
| pdf URL | `https://arxiv.org/pdf/2401.12345v2.pdf` | `2401.12345` |
| pdf URL, no version | `http://arxiv.org/pdf/2401.12345` | `2401.12345` |
| Pre-2007 format | `cs/0701001` | `cs/0701001` |
| Pre-2007, versioned | `cs/0701001v1` | `cs/0701001` |

Validation is done synchronously via a regex in
[`normalize_arxiv_id()`](../app/services/arxiv.py). Malformed ids are
rejected with `422 Unprocessable Entity` before any Celery task is
dispatched — no background work is wasted on bad input.

### Error responses

| Status | When | Body |
|---|---|---|
| `422` | `arxiv_id` is missing, blank, or doesn't match the regex | `{"detail": [{"msg": "not a recognizable arXiv id: ...", ...}]}` |
| `429` | More than 5 requests per minute from the same IP | `{"error": "Rate limit exceeded: 5 per 1 minute"}` |

---

## Response

```json
{
  "message": "arXiv ingestion started.",
  "arxiv_id": "2401.12345",
  "doc_id": "arxiv-2401.12345",
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

| Field | Description |
|---|---|
| `arxiv_id` | Canonical id with version stripped (e.g. `2401.12345`, not `2401.12345v2`) |
| `doc_id` | `arxiv-{canonical_id}` — used as the paper_id throughout the pipeline |
| `task_id` | Celery task id. Poll `GET /api/v1/status/{task_id}` for progress. |

---

## Polling

Use the same status endpoint as for PDF uploads:

```bash
curl http://localhost:8000/api/v1/status/{task_id}
```

The task transitions through these states:

```
PENDING → DOWNLOADING → PROCESSING → completed | partial | failed
```

When the task is complete, the `result` field contains the pipeline
output — same shape as `POST /api/v1/upload`'s result, plus an `arxiv`
block carrying the resolved metadata (title, authors, abstract, DOI,
categories, published/updated dates):

```json
{
  "status": "completed",
  "arxiv_id": "2401.12345",
  "doc_id": "arxiv-2401.12345",
  "arxiv": {
    "arxiv_id": "2401.12345",
    "title": "Mixtral of Experts...",
    "authors": ["Albert Q. Jiang", "..."],
    "doi": "10.48550/arXiv.2401.12345",
    "primary_category": "cs.CL",
    "categories": ["cs.CL", "cs.LG"],
    "published": "2024-01-22T00:00:00Z",
    "pdf_url": "https://arxiv.org/pdf/2401.12345v2.pdf"
  },
  "chunks_count": 42,
  "entities_count": 18,
  "relations_count": 7,
  "graph_nodes_count": 25,
  "graph_edges_count": 12,
  "auto_inserted_count": 15,
  "queued_for_review_count": 3,
  "openalex_enriched": true,
  "pipeline_steps": [...]
}
```

---

## How it works

### 1. Synchronous validation

The endpoint calls `normalize_arxiv_id(body.arxiv_id)` which runs the
input through a regex. If the regex doesn't match, Pydantic's field
validator raises `ValueError` and FastAPI returns `422` before the
request handler body executes. No Celery task is dispatched.

### 2. Task dispatch

The canonical id is used to construct `doc_id = "arxiv-{canonical_id}"`
and the raw user input is passed to
`celery_app.send_task("app.worker.tasks.process_arxiv_task", args=[body.arxiv_id])`.
A `PENDING` result is pre-stored in the Celery backend so that
`GET /api/v1/status/{task_id}` can distinguish a queued task from a
nonexistent one.

### 3. PDF download (in the worker)

The task calls `ArxivClient.download_pdf(raw_id, dest_path)`:

1. Resolves metadata via arXiv's Atom export API
   (`http://export.arxiv.org/api/query?id_list={id}`).
2. Streams the PDF from `https://arxiv.org/pdf/{versioned_id}.pdf` to
   a `.partial` temp file.
3. On success, atomically renames `.partial` → final path via
   `os.replace()`. A network failure or 404 leaves no half-written
   file behind.
4. A class-level throttle enforces a 3-second gap between arXiv API
   calls, honoring arXiv's polite-use policy even when multiple workers
   are running.

### 4. Pipeline execution

The task constructs a `PaperIngestionPipeline` with the same optional
collaborators as `process_pdf_task` (Neo4j, Weaviate, OpenAlex, LLM
extractor, draft sink — each returns `None` if its backing service is
unavailable) and calls `pipeline.process(paper_id=doc_id, file_path=file_path)`.

### 5. Idempotency

If the PDF already exists at
`{UPLOAD_DIR}/arxiv-{canonical_id}.pdf` (from a previous ingest), the
download is skipped and only metadata is refetched. The pipeline
re-processes the file in full — same semantics as `force=true` on the
upload endpoint.

### Retry semantics

| Failure | Behavior |
|---|---|
| Invalid id (regex doesn't match) | Hard fail — no retry |
| arXiv 404 (no entry for this id) | Hard fail — no retry |
| arXiv transport error (timeout, 5xx, network) | Retry with exponential backoff (base 10s, jitter), max 2 retries |
| Pipeline exception | Hard fail — no retry (the pipeline handles per-step errors internally and returns `FAILED`/`PARTIAL`) |

---

## Configuration

The arXiv endpoint has no endpoint-specific configuration — it uses
the same settings as the rest of the pipeline:

| Setting | Default | Used for |
|---|---|---|
| `UPLOAD_DIR` | `/app/uploads` | Where downloaded PDFs are stored |
| `EXTRACTION_PROVIDER` | `heuristic` | Whether LLM extraction runs (`heuristic` / `llm` / `hybrid`) |
| `OPENALEX_ENABLED` | `true` | Whether OpenAlex metadata enrichment runs after ingestion |
| `AUTH_REQUIRED` | `false` | Whether the endpoint requires a JWT bearer token |
| `CORS_ORIGINS` | `["http://localhost:3000", "http://localhost:4200"]` | Allowed browser origins |

The rate limit (5 requests/minute per IP) is set via the
`@limiter.limit("5/minute")` decorator on the endpoint and matches the
upload endpoint's limit. To change it, edit the decorator in
[`app/api/arxiv_routes.py`](../app/api/arxiv_routes.py).

---

## Security considerations

### Input validation (no SSRF)

The endpoint does **not** accept user-supplied URLs. The `arxiv_id`
field is validated against a strict regex that only accepts arXiv id
formats (with optional URL prefix). The PDF URL is constructed
server-side from the canonical id as
`https://arxiv.org/pdf/{versioned_id}.pdf`. An attacker cannot use
this endpoint to make the server fetch arbitrary URLs — the regex
rejects anything that isn't an arXiv id before any HTTP request is
made.

### Rate limiting

5 requests per minute per client IP, enforced via
[`slowapi`](https://slowapi.readthedocs.io/). This is the same limit
as the upload endpoint and is sufficient for interactive use (a
researcher ingesting papers one at a time) while preventing abuse.

### Atomic file writes

The PDF download streams to a `.partial` file first and renames it to
the final path only on success. A crash or network failure mid-download
leaves no half-written file that could be mistaken for a complete PDF
by a future re-ingest.

### arXiv polite-use policy

The `ArxivClient` enforces a 3-second gap between requests to arXiv's
export API via a class-level timestamp. This honors arXiv's
[API rate limit guidelines](https://info.arxiv.org/help/api/tou.html)
and prevents the IP from being blocked. A `User-Agent` header
identifying Papercraft is sent with every request — arXiv requires
this for some PDFs.

### No credential exposure

The endpoint does not pass any API keys, tokens, or credentials to
arXiv. arXiv's API is public and requires no authentication.

---

## Usage examples

### curl

```bash
# Ingest a paper
curl -X POST http://localhost:8000/api/v1/ingest/arxiv \
  -H 'Content-Type: application/json' \
  -d '{"arxiv_id": "1706.03762"}'

# Poll for status
curl http://localhost:8000/api/v1/status/{task_id}
```

### Python

```python
import httpx

resp = httpx.post(
    "http://localhost:8000/api/v1/ingest/arxiv",
    json={"arxiv_id": "https://arxiv.org/abs/1706.03762"},
)
data = resp.json()
# {"arxiv_id": "1706.03762", "doc_id": "arxiv-1706.03762", "task_id": "..."}

# Poll until complete
import time
while True:
    status = httpx.get(f"http://localhost:8000/api/v1/status/{data['task_id']}").json()
    if status["status"] in ("completed", "partial", "failed"):
        print(status)
        break
    time.sleep(2)
```

---

## Related

- [Architecture overview](./architecture.md) — where this endpoint fits in the pipeline
- [Developer setup](./setup.md) — how to run the stack locally
- [Decisions log](./decisions.md) — design rationale for each component
- [`app/api/arxiv_routes.py`](../app/api/arxiv_routes.py) — endpoint implementation
- [`app/services/arxiv.py`](../app/services/arxiv.py) — arXiv HTTP client
- [`app/worker/arxiv_tasks.py`](../app/worker/arxiv_tasks.py) — Celery task
