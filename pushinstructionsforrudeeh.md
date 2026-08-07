# Papercraft: applying the implementation history to your repo

Hi rudeeh — Anshul is building the Papercraft implementation (based on your spec) on top of his working DocRAG v3 GraphRAG backend. This bundle grafts that implementation's full git history onto your `Papercraft` repo's `main`. Your README and LICENSE stay at the root exactly as they are; DocRAG's old readme moves to `docs/DOCRAG_README.md`.

The file `papercraft-main.bundle` is a self-contained git archive — no third-party access, no tokens involved. You push it with your own normal GitHub auth. The update is **fast-forward only**: your current `main` is an ancestor of the new history, so nothing you've written is rewritten or force-pushed.

## Commands (5 minutes)

```bash
# 1. Fresh clone of your repo (or use an existing clone on main)
git clone https://github.com/rudeeh/Papercraft
cd Papercraft

# 2. Verify the bundle is complete and valid
git bundle verify ../papercraft-main.bundle

# 3. Pull the bundled history into a local branch
git fetch ../papercraft-main.bundle main:incoming-papercraft

# 4. Inspect it if you like
git log --oneline --graph incoming-papercraft | head -20

# 5. Fast-forward your main onto it (fails safely if anything unexpected)
git checkout main
git merge --ff-only incoming-papercraft

# 6. Push
git push origin main
```

Adjust the `../papercraft-main.bundle` path to wherever you saved the file.

## What you'll end up with

- Root: your `README.md` (spec) + `LICENSE`, unchanged
- `app/` — FastAPI + Celery + Neo4j + Qdrant GraphRAG backend (54 commits of history)
- `tests/` — 315 passing tests
- `evaluation/`, `docs/`, `scripts/`, `docker-compose.yml` — eval harness, docs, infra
- `docs/DOCRAG_README.md` — the original DocRAG documentation

Next changes coming from Anshul's side: Weaviate vector layer (replacing Qdrant, per your spec), then PostgreSQL/MinIO/auth scaffolding, OpenAlex enrichment, LLM extraction with confidence routing, attestation API, and a minimal Next.js frontend.

## One security thing

A GitHub personal access token of yours (`ghp_DhF7...`) was shared in a chat conversation to set this up. It wasn't needed and wasn't used — but treat it as exposed and **revoke it now**: GitHub → Settings → Developer settings → Personal access tokens. If Anshul later needs direct push access, prefer adding him as a collaborator on the repo, or issue a fine-grained token scoped to just this repo with a short expiry.
