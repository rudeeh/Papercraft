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
  PaperGraphResponse
} from '../models/api.models';

const STORAGE_KEY = 'rag_recent_tasks';
const API_URL_KEY = 'apiUrl';
const DEFAULT_API_URL = 'https://docrag-2gvg.onrender.com';
const KEEP_ALIVE_INTERVAL = 14 * 60 * 1000; // 14 minutes

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private keepAliveInterval: ReturnType<typeof setInterval> | null = null;

  apiUrl = signal<string>(this.loadApiUrl());
  healthStatus = signal<HealthStatus>({ online: false });
  recentTasks = signal<RecentTask[]>(this.loadRecentTasks());

  constructor(private http: HttpClient) {
    this.startKeepAlive();
    this.checkHealth();
    setInterval(() => this.checkHealth(), 30000);
  }

  private loadApiUrl(): string {
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem(API_URL_KEY) || DEFAULT_API_URL;
    }
    return DEFAULT_API_URL;
  }

  setApiUrl(url: string): void {
    const cleanUrl = url.replace(/\/$/, '');
    this.apiUrl.set(cleanUrl);
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(API_URL_KEY, cleanUrl);
    }
    this.checkHealth();
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
    return this.http.post<ChatResponse>(`${this.apiUrl()}/api/v1/chat`, request).pipe(
      catchError(this.handleError)
    );
  }

  graphQuery(request: GraphQueryRequest): Observable<GraphQueryResponse> {
    return this.http.post<GraphQueryResponse>(`${this.apiUrl()}/api/v1/graph-query`, request).pipe(
      catchError(this.handleError)
    );
  }

  getPaperGraph(paperId: string): Observable<PaperGraphResponse> {
    return this.http.get<PaperGraphResponse>(`${this.apiUrl()}/api/v1/papers/${paperId}/graph`).pipe(
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
