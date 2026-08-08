import { Injectable, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Observable, catchError, throwError, tap } from 'rxjs';
import {
  UploadResponse,
  TaskStatus,
  ChatRequest,
  ChatResponse,
  RecentTask,
  HealthStatus,
  GraphQueryRequest,
  GraphQueryResponse,
  CitationGraphResponse,
  LlmStatus
} from '../models/api.models';

const STORAGE_KEY = 'rag_recent_tasks';
const API_URL_KEY = 'apiUrl';
const LLM_API_KEY_STORAGE = 'openrouter_api_key';
const DEPLOYED_API_URL = 'https://docrag-2gvg.onrender.com';
const LOCAL_API_PORT = '8000';
const KEEP_ALIVE_INTERVAL = 14 * 60 * 1000; // 14 minutes

/**
 * Where to point when the user hasn't picked an API URL yet.
 *
 * Served from localhost (the docker-compose setup puts the frontend on
 * :8080 and the API on :8000), default to the API on this same host --
 * hardcoding the deployed URL there meant every fresh local load fired
 * cross-origin requests at a remote backend, showing "Offline" and
 * filling the console with CORS errors until you manually retyped the
 * URL. Reusing ``hostname`` rather than a literal also keeps whichever
 * form you browsed with ("localhost" vs "127.0.0.1") intact, which
 * matters when only one of the two resolves to the running container.
 *
 * Anywhere else (e.g. the frontend deployed to Vercel), fall back to the
 * deployed backend as before.
 */
function defaultApiUrl(): string {
  if (typeof window === 'undefined') {
    return DEPLOYED_API_URL;
  }
  const { protocol, hostname } = window.location;
  if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]') {
    return `${protocol}//${hostname}:${LOCAL_API_PORT}`;
  }
  return DEPLOYED_API_URL;
}

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private keepAliveInterval: ReturnType<typeof setInterval> | null = null;

  apiUrl = signal<string>(this.loadApiUrl());
  healthStatus = signal<HealthStatus>({ online: false });
  recentTasks = signal<RecentTask[]>(this.loadRecentTasks());
  llmApiKey = signal<string>(this.loadLlmApiKey());
  // Optimistic default so the "no server key" hint doesn't flash on load;
  // flips to false shortly after if the server really has none configured.
  llmStatus = signal<LlmStatus>({ server_key_configured: true });

  constructor(private http: HttpClient) {
    this.startKeepAlive();
    this.checkHealth();
    this.checkLlmStatus();
    setInterval(() => this.checkHealth(), 30000);
  }

  private loadApiUrl(): string {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem(API_URL_KEY) || defaultApiUrl();
    }
    return defaultApiUrl();
  }

  setApiUrl(url: string): void {
    const cleanUrl = url.replace(/\/$/, '');
    this.apiUrl.set(cleanUrl);
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(API_URL_KEY, cleanUrl);
    }
    this.checkHealth();
    this.checkLlmStatus();
  }

  private loadLlmApiKey(): string {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem(LLM_API_KEY_STORAGE) || '';
    }
    return '';
  }

  setLlmApiKey(key: string): void {
    const trimmed = key.trim();
    this.llmApiKey.set(trimmed);
    if (typeof localStorage !== 'undefined') {
      if (trimmed) {
        localStorage.setItem(LLM_API_KEY_STORAGE, trimmed);
      } else {
        localStorage.removeItem(LLM_API_KEY_STORAGE);
      }
    }
  }

  checkLlmStatus(): void {
    this.http.get<LlmStatus>(`${this.apiUrl()}/api/v1/llm-status`).subscribe({
      next: (status) => this.llmStatus.set(status),
      error: () => this.llmStatus.set({ server_key_configured: true }) // fail open -- don't nag if we can't tell
    });
  }

  private startKeepAlive(): void {
    this.ping();
    this.keepAliveInterval = setInterval(() => this.ping(), KEEP_ALIVE_INTERVAL);
  }

  private ping(): void {
    this.http.get(`${this.apiUrl()}/api/v1/health`).subscribe({
      next: () => console.log(`[Keep-alive] Pinged server at ${new Date().toLocaleTimeString()}`),
      error: (err) => console.log(`[Keep-alive] Ping failed: ${err.message}`)
    });
  }

  checkHealth(): void {
    this.http.get(`${this.apiUrl()}/api/v1/health`).subscribe({
      next: () => this.healthStatus.set({ online: true }),
      error: () => this.healthStatus.set({ online: false })
    });
  }

  uploadPdf(file: File, force: boolean = false): Observable<UploadResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const url = `${this.apiUrl()}/api/v1/upload${force ? '?force=true' : ''}`;

    return this.http.post<UploadResponse>(url, formData).pipe(
      tap(response => {
        if (response.task_id) {
          this.saveTask({
            task_id: response.task_id,
            doc_id: response.doc_id,
            filename: file.name,
            timestamp: new Date().toISOString(),
            status: 'pending'
          });
        }
      }),
      catchError(this.handleError)
    );
  }

  getTaskStatus(taskId: string): Observable<TaskStatus> {
    return this.http.get<TaskStatus>(`${this.apiUrl()}/api/v1/status/${taskId}`).pipe(
      tap(response => {
        this.updateTaskStatus(taskId, response.status.toLowerCase());
      }),
      catchError(this.handleError)
    );
  }

  chat(request: ChatRequest): Observable<ChatResponse> {
    const payload = { ...request, api_key: request.api_key || this.llmApiKey() || undefined };
    return this.http.post<ChatResponse>(`${this.apiUrl()}/api/v1/chat`, payload).pipe(
      catchError(this.handleError)
    );
  }

  graphQuery(request: GraphQueryRequest): Observable<GraphQueryResponse> {
    const payload = { ...request, api_key: request.api_key || this.llmApiKey() || undefined };
    return this.http.post<GraphQueryResponse>(`${this.apiUrl()}/api/v1/graph-query`, payload).pipe(
      catchError(this.handleError)
    );
  }

  getCitationGraph(): Observable<CitationGraphResponse> {
    return this.http.get<CitationGraphResponse>(`${this.apiUrl()}/api/v1/citation-graph`).pipe(
      catchError(this.handleError)
    );
  }

  private loadRecentTasks(): RecentTask[] {
    if (typeof localStorage !== 'undefined') {
      const stored = localStorage.getItem(STORAGE_KEY);
      return stored ? JSON.parse(stored) : [];
    }
    return [];
  }

  private saveTask(task: RecentTask): void {
    let tasks = this.loadRecentTasks();
    const existingIndex = tasks.findIndex(t => t.task_id === task.task_id);

    if (existingIndex >= 0) {
      tasks[existingIndex] = task;
    } else {
      tasks.unshift(task);
    }

    tasks = tasks.slice(0, 10);

    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
    }
    this.recentTasks.set(tasks);
  }

  private updateTaskStatus(taskId: string, status: string): void {
    const tasks = this.loadRecentTasks();
    const task = tasks.find(t => t.task_id === taskId);

    if (task) {
      task.status = status;
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
      }
      this.recentTasks.set(tasks);
    }
  }

  clearRecentTasks(): void {
    if (typeof localStorage !== 'undefined') {
      localStorage.removeItem(STORAGE_KEY);
    }
    this.recentTasks.set([]);
  }

  private handleError(error: HttpErrorResponse): Observable<never> {
    let errorMessage = 'An error occurred';

    if (error.error instanceof ErrorEvent) {
      errorMessage = error.error.message;
    } else if (error.error?.detail) {
      errorMessage = error.error.detail;
    } else if (error.message) {
      errorMessage = error.message;
    }

    return throwError(() => new Error(errorMessage));
  }
}
