'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, api, type TaskStatus } from '@/lib/api';

const TERMINAL = ['SUCCESS', 'FAILURE', 'REVOKED'];
const POLL_MS = 2000;

export default function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async (id: string) => {
    try {
      const next = await api.status(id);
      setTask(next);
      if (!TERMINAL.includes(next.status)) {
        timer.current = setTimeout(() => poll(id), POLL_MS);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  // Clearing the timer on unmount stops the poll loop from outliving the
  // page and calling setState on a dead component.
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setTask(null);
    try {
      const result = await api.upload(file);
      if (result.task_id) {
        setTaskId(result.task_id);
        poll(result.task_id);
      } else {
        // Re-upload of a PDF already ingested: the backend short-circuits
        // and returns no task, which is a result, not an error.
        setError(result.message);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card">
        <h1 className="mb-1 text-base font-semibold text-slate-100">Ingest a paper</h1>
        <p className="mb-4 text-sm text-muted">
          The PDF is parsed, enriched against OpenAlex, and turned into a paper graph.
          Low-confidence extractions land in the review queue rather than the graph.
        </p>
        <form onSubmit={submit} className="flex flex-wrap items-center gap-3">
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="input flex-1 file:mr-3 file:rounded file:border-0 file:bg-edge
                       file:px-3 file:py-1 file:text-sm file:text-slate-200"
          />
          <button type="submit" className="btn btn-primary" disabled={!file || busy}>
            {busy ? 'Uploading…' : 'Upload'}
          </button>
        </form>
      </section>

      {error && (
        <div className="card border-bad/50 text-sm text-bad" role="alert">
          {error}
        </div>
      )}

      {taskId && task && <TaskPanel task={task} />}
    </>
  );
}

function TaskPanel({ task }: { task: TaskStatus }) {
  const result = task.result;
  return (
    <section className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-100">Processing</h2>
        <span className="pill">{task.status}</span>
      </div>

      {result && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
          <Stat label="Entities" value={result.entities_count} />
          <Stat label="Relations" value={result.relations_count} />
          <Stat label="Graph nodes" value={result.graph_nodes_count} />
          <Stat label="Graph edges" value={result.graph_edges_count} />
          <Stat label="Queued for review" value={result.queued_for_review_count} />
          <Stat label="OpenAlex" value={result.openalex_enriched ? 'enriched' : 'no match'} />
        </dl>
      )}

      {result?.pipeline_steps && (
        <ol className="space-y-1 text-xs">
          {result.pipeline_steps.map((step) => (
            <li key={step.step} className="flex items-center gap-2">
              <span
                className={
                  step.status === 'success'
                    ? 'text-ok'
                    : step.status === 'error'
                      ? 'text-bad'
                      : 'text-muted'
                }
              >
                ●
              </span>
              <span className="w-44 text-muted">{step.step}</span>
              <span className="text-muted">{step.duration_ms.toFixed(1)} ms</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: number | string | undefined }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="font-medium text-slate-100">{value ?? '—'}</dd>
    </div>
  );
}
