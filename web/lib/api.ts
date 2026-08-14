/**
 * Typed client for the Papercraft API.
 *
 * Every call goes through `request`, which turns a non-2xx into a thrown
 * ApiError carrying the backend's `detail` string. The API is deliberate
 * about its status codes -- 401 for a missing LLM key, 409 for a draft
 * already resolved, 422 for a mistyped filter -- and surfacing those
 * verbatim is more useful to a curator than "something went wrong".
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') ?? 'http://localhost:8000';

const BASE = `${API_URL}/api/v1`;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...init?.headers,
      },
      cache: 'no-store',
    });
  } catch {
    // A network-level failure has no status and no body to read.
    throw new ApiError(`Cannot reach the API at ${API_URL}. Is the backend running?`, 0);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d: any) => d.msg).join('; ');
    } catch {
      /* Non-JSON error body; the status text stands. */
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------

export interface UploadResult {
  message: string;
  doc_id: string;
  task_id?: string;
  status?: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  info?: Record<string, unknown>;
  result?: {
    status: string;
    entities_count?: number;
    relations_count?: number;
    graph_nodes_count?: number;
    graph_edges_count?: number;
    queued_for_review_count?: number;
    openalex_enriched?: boolean;
    pipeline_steps?: { step: string; status: string; duration_ms: number }[];
  };
}

export interface GraphFact {
  subject?: { name?: string; type?: string; paper_id?: string };
  relation?: string;
  object?: { name?: string; type?: string; paper_id?: string };
  evidence?: string;
  source_paper_ids?: string[];
}

export interface GraphQueryResult {
  answer: string;
  sources: { paper_id: string; title?: string }[];
  retrieval_trace: {
    query_type?: string;
    graph_facts?: GraphFact[];
    vector_results?: unknown[];
    citation_paths?: unknown[];
    source_paper_ids?: string[];
    confidence_notes?: string[];
  };
}

export interface Draft {
  id: string;
  paper_id: string;
  kind: 'entity' | 'relation';
  payload: Record<string, any>;
  confidence: number;
  routing: string;
  status: string;
  extracted_by: string;
  attestation_score: number;
  created_at: string | null;
}

export interface QueueStats {
  pending: number;
  promoted: number;
  rejected: number;
  total: number;
}

// ---------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------

export const api = {
  health: () => request<{ status: string }>('/health'),

  upload: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<UploadResult>('/upload', { method: 'POST', body: form });
  },

  status: (taskId: string) => request<TaskStatus>(`/status/${taskId}`),

  graphQuery: (query: string, apiKey?: string) =>
    request<GraphQueryResult>('/graph-query', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: 10, api_key: apiKey || undefined }),
    }),

  drafts: (params: { paper_id?: string; kind?: string; limit?: number } = {}) => {
    const search = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v != null && search.set(k, String(v)));
    const qs = search.toString();
    return request<{ drafts: Draft[] }>(`/curation/drafts${qs ? `?${qs}` : ''}`);
  },

  queueStats: () => request<QueueStats>('/curation/stats'),

  attest: (draftId: string, vote: 1 | -1) =>
    request<{ score: number; promoted: boolean }>(`/curation/drafts/${draftId}/attest`, {
      method: 'POST',
      body: JSON.stringify({ vote }),
    }),

  promote: (draftId: string) =>
    request<Draft>(`/curation/drafts/${draftId}/promote`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  reject: (draftId: string) =>
    request<Draft>(`/curation/drafts/${draftId}/reject`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),

  leaderboard: () =>
    request<{ curators: { user_id: string; display_name: string; reputation: number }[] }>(
      '/curation/leaderboard',
    ),
};

// ---------------------------------------------------------------------------
// Paper detail (split-screen UI)
// ---------------------------------------------------------------------------

export interface PaperSection {
  heading: string;
  text: string;
}

export interface PaperEntity {
  node_type: string;
  name: string;
  properties: Record<string, unknown>;
}

export interface PaperCitation {
  title?: string;
  doi?: string;
  arxiv_id?: string;
  is_stub?: boolean;
  abstract?: string;
  citation_count?: number;
  influential_citation_count?: number;
  tldr?: { text: string; model: string; source: string };
}

export interface PaperExtraction {
  doc_id: string;
  title?: string;
  abstract?: string;
  sections: PaperSection[];
  entities: PaperEntity[];
  citations: PaperCitation[];
  graph_stats: Record<string, number>;
  has_pdf: boolean;
  extraction_available: boolean;
}

export const paperApi = {
  pdfUrl: (docId: string) => `${BASE}/papers/${docId}/pdf`,

  extraction: (docId: string) =>
    request<PaperExtraction>(`/papers/${docId}/extraction`),
};
