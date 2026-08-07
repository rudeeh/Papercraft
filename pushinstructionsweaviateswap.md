# Milestone 2 for rudeeh: Weaviate vector-store swap

One new commit on top of what you already pushed. Same drill as last time — fast-forward only, your own auth, no tokens.

```bash
cd Papercraft            # your existing clone, on main
git bundle verify ../papercraft-weaviate-swap.bundle
git fetch ../papercraft-weaviate-swap.bundle main:incoming-weaviate
git log --oneline incoming-weaviate | head -5   # inspect if you like
git merge --ff-only incoming-weaviate
git push origin main
```

(If you deleted your clone: `git clone https://github.com/rudeeh/Papercraft && cd Papercraft` first.)

## What's in it (commit `69faa4c`)

Qdrant is fully replaced by **Weaviate 1.28** (your spec's choice) across the backend: new `app/storage/weaviate_client.py`, `VectorRepository` and the legacy `/chat` path swapped, docker-compose now runs a `weaviate` service (HTTP on host **8082**, gRPC 50051 — 8080 is still held by the old Angular frontend), and there's an ADR in `docs/decisions.md` explaining exactly what was preserved (payload schema, deterministic chunk IDs, score semantics, dim-mismatch recreate).

Verified: 326 unit tests passing, plus a live smoke test against a real Weaviate 1.28.4 instance (store → filtered search → hybrid graph-filter search → paper aggregation → idempotent re-ingestion → dimension-change recreate).

After pulling, a local `docker compose up -d --build` will fetch the Weaviate image on first run.
