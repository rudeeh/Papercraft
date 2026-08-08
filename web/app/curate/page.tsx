'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, api, type Draft, type QueueStats } from '@/lib/api';

export default function CuratePage() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [stats, setStats] = useState<QueueStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [queue, counts] = await Promise.all([api.drafts({ limit: 50 }), api.queueStats()]);
      setDrafts(queue.drafts);
      setStats(counts);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(draftId: string, action: () => Promise<unknown>) {
    setPending(draftId);
    setError(null);
    try {
      await action();
      // Reload rather than patching local state: an attestation can cross
      // the promote threshold and resolve the draft server-side, so the
      // server's view is the only one that is definitely right.
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <>
      <section className="card">
        <h1 className="mb-1 text-base font-semibold text-slate-100">Review queue</h1>
        <p className="text-sm text-muted">
          Extractions the confidence router would not wave through. Two net upvotes promote
          one into the graph; two net downvotes discard it.
        </p>
        {stats && (
          <dl className="mt-4 flex gap-6 text-sm">
            <Stat label="Pending" value={stats.pending} />
            <Stat label="Promoted" value={stats.promoted} />
            <Stat label="Rejected" value={stats.rejected} />
          </dl>
        )}
      </section>

      {error && (
        <div className="card border-bad/50 text-sm text-bad" role="alert">
          {error}
        </div>
      )}

      {loading && <p className="text-sm text-muted">Loading…</p>}

      {!loading && drafts.length === 0 && !error && (
        <p className="text-sm text-muted">
          Nothing awaiting review. Ingest a paper with <code>EXTRACTION_PROVIDER=hybrid</code>{' '}
          to populate the queue.
        </p>
      )}

      {drafts.map((draft) => (
        <DraftCard
          key={draft.id}
          draft={draft}
          busy={pending === draft.id}
          onAttest={(vote) => act(draft.id, () => api.attest(draft.id, vote))}
          onPromote={() => act(draft.id, () => api.promote(draft.id))}
          onReject={() => act(draft.id, () => api.reject(draft.id))}
        />
      ))}
    </>
  );
}

function DraftCard({
  draft,
  busy,
  onAttest,
  onPromote,
  onReject,
}: {
  draft: Draft;
  busy: boolean;
  onAttest: (vote: 1 | -1) => void;
  onPromote: () => void;
  onReject: () => void;
}) {
  const { payload } = draft;
  const summary =
    draft.kind === 'relation'
      ? `${payload.source} —${payload.relation}→ ${payload.target}`
      : `${payload.name} (${payload.type})`;

  return (
    <section className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="text-sm text-accent">{summary}</code>
        <div className="flex items-center gap-2">
          <span className="pill">{draft.routing}</span>
          <span className="pill">{(draft.confidence * 100).toFixed(0)}% confident</span>
          <span className="pill">score {draft.attestation_score}</span>
        </div>
      </div>

      {payload.evidence && (
        <blockquote className="border-l-2 border-edge pl-3 text-sm italic text-muted">
          {payload.evidence}
        </blockquote>
      )}

      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>paper {draft.paper_id.slice(0, 12)}</span>
        <span>·</span>
        <span>by {draft.extracted_by}</span>
      </div>

      <div className="flex flex-wrap gap-2">
        <button className="btn" onClick={() => onAttest(1)} disabled={busy}>
          Attest
        </button>
        <button className="btn" onClick={() => onAttest(-1)} disabled={busy}>
          Dispute
        </button>
        <button className="btn btn-primary" onClick={onPromote} disabled={busy}>
          Promote
        </button>
        <button className="btn" onClick={onReject} disabled={busy}>
          Reject
        </button>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="font-medium text-slate-100">{value}</dd>
    </div>
  );
}
