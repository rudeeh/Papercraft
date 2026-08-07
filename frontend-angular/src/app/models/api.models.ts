export interface UploadResponse {
  message: string;
  task_id?: string;
  doc_id?: string;
  detail?: string;
}

export interface TaskStatus {
  task_id: string;
  status: 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE';
  result?: unknown;
  error?: string;
}

export interface Citation {
  filename?: string;
  page?: number;
  doc_id?: string;
  text_snippet: string;
}

export interface ChatRequest {
  query: string;
  doc_id?: string;
}

export interface ChatResponse {
  answer: string;
  citations?: Citation[];
}

export interface RecentTask {
  task_id: string;
  doc_id?: string;
  filename: string;
  timestamp: string;
  status: string;
}

export interface HealthStatus {
  online: boolean;
  message?: string;
}

// ---------------------------------------------------------------------
// GraphRAG (Phase 16/17)
// ---------------------------------------------------------------------

export interface GraphQueryRequest {
  query: string;
  project_id?: string;
  top_k?: number;
}

export interface GraphNodeRef {
  name: string;
  type: string;
  paper_id?: string | null;
}

export interface GraphFact {
  subject: GraphNodeRef;
  relation: string;
  object: GraphNodeRef;
  evidence?: string | null;
  source_paper_ids: string[];
}

export interface VectorResult {
  id: string;
  score: number;
  text?: string | null;
  paper_id?: string | null;
  section?: string | null;
  node_type?: string | null;
  node_name?: string | null;
}

export interface CitationPathStep {
  paper_id: string;
  title?: string | null;
  depth: number;
  path: string[];
  direction: 'forward' | 'backward';
}

export interface SourcePaper {
  paper_id: string;
  title?: string | null;
}

export interface RetrievalTrace {
  query_type: string;
  graph_facts: GraphFact[];
  vector_results: VectorResult[];
  citation_paths: CitationPathStep[];
  source_paper_ids: string[];
  confidence_notes: string[];
}

export interface GraphQueryResponse {
  answer: string;
  sources: SourcePaper[];
  retrieval_trace: RetrievalTrace;
}

export interface PaperGraphNode {
  id: string;
  type: string;
  name?: string;
  [key: string]: unknown;
}

export interface PaperGraphEdge {
  source: string;
  source_type: string;
  type: string;
  target: string;
  target_type: string;
  properties?: Record<string, unknown>;
}

export interface PaperGraphResponse {
  paper_id: string;
  nodes: PaperGraphNode[];
  edges: PaperGraphEdge[];
}
