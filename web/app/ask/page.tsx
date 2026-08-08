'use client';

import { useState } from 'react';
import { ApiError, api, type GraphQueryResult } from '@/lib/api';

export default function AskPage() {
  const [query, setQuery] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [result, setResult] = useState<GraphQueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await api.graphQuery(query, apiKey));
      setNeedsKey(false);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        // The backend answers 401 specifically when no OpenRouter key is
        // configured server-side, so that is the one error worth turning
        // into an input rather than just a message.
        setNeedsKey(err.status === 401);
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card">
        <h1 className="mb-1 text-base font-semibold text-slate-100">Ask the graph</h1>
        <p className="mb-4 text-sm text-muted">
          Questions are routed to graph traversal, vector search, or both, and answered
          only from what was retrieved.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            className="input"
            placeholder="How did attention mechanisms evolve from RNNs to Transformers?"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {needsKey && (
            <input
              className="input"
              type="password"
              placeholder="OpenRouter API key (the server has none configured)"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
          <button type="submit" className="btn btn-primary" disabled={!query.trim() || busy}>
            {busy ? 'Retrieving…' : 'Ask'}
          </button>
        </form>
      </section>

      {error && (
        <div className="card border-bad/50 text-sm text-bad" role="alert">
          {error}
        </div>
      )}

      {result && <Answer result={result} />}
    </>
  );
}

function Answer({ result }: { result: GraphQueryResult }) {
  const trace = result.retrieval_trace ?? {};
  const facts = trace.graph_facts ?? [];

  return (
    <>
      <section className="card space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-100">Answer</h2>
          {trace.query_type && <span className="pill">{trace.query_type}</span>}
        </div>
        <p className="whitespace-pre-wrap text-sm leading-relaxed">{result.answer}</p>
      </section>

      {result.sources.length > 0 && (
        <section className="card">
          <h2 className="mb-2 text-sm font-semibold text-slate-100">Sources</h2>
          <ul className="space-y-1 text-sm">
            {result.sources.map((source) => (
              <li key={source.paper_id} className="text-muted">
                <span className="text-slate-200">{source.title || 'Untitled'}</span>{' '}
                <code className="text-xs">{source.paper_id.slice(0, 12)}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {facts.length > 0 && (
        <section className="card">
          <h2 className="mb-2 text-sm font-semibold text-slate-100">Graph facts used</h2>
          <ul className="space-y-2 text-sm">
            {facts.map((fact, index) => (
              <li key={index}>
                <code className="text-accent">
                  {fact.subject?.name} —{fact.relation}→ {fact.object?.name}
                </code>
                {fact.evidence && (
                  <p className="mt-0.5 text-xs italic text-muted">“{fact.evidence}”</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {trace.confidence_notes && trace.confidence_notes.length > 0 && (
        <section className="card">
          <h2 className="mb-2 text-sm font-semibold text-slate-100">Confidence notes</h2>
          <ul className="list-inside list-disc space-y-1 text-sm text-muted">
            {trace.confidence_notes.map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
