import { Component, inject, signal, input, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { TaskStatus } from '../../models/api.models';

@Component({
  selector: 'app-status',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>Check a task by ID</h2>
      <p class="hint">Uploads made here already show their status below in "Recent Uploads" -- use this only if you have a task ID from somewhere else (e.g. a script).</p>

      <div class="form-group">
        <input
          type="text"
          [(ngModel)]="taskId"
          placeholder="Enter task ID"
          class="input"
        />
      </div>

      <button
        class="btn btn-secondary"
        (click)="checkStatus()"
        [disabled]="!taskId || isLoading()"
      >
        {{ isLoading() ? 'Checking...' : 'Check Status' }}
      </button>

      @if (result()) {
        <div class="result" [class]="getResultClass()">
          <div class="status-header">
            <span class="status-label">Task Status:</span>
            <span class="status-badge" [class]="result()!.status.toLowerCase()">
              {{ result()!.status }}
            </span>
          </div>
          <pre>{{ result() | json }}</pre>
        </div>
      }

      @if (error()) {
        <div class="result error">
          <p>{{ error() }}</p>
        </div>
      }
    </div>
  `,
  styles: [`
    .card {
      background: var(--color-white);
      border: 1px solid var(--color-border);
      border-radius: 8px;
      padding: 1.5rem;
    }

    h2 {
      margin: 0 0 0.5rem;
      font-size: 1.125rem;
      font-weight: 600;
      color: var(--color-black);
    }

    .hint {
      margin: 0 0 1.25rem;
      color: var(--color-medium-gray);
      font-size: 0.8125rem;
    }

    .form-group {
      margin-bottom: 1rem;
    }

    .input {
      width: 100%;
      padding: 0.625rem 0.875rem;
      border: 1px solid var(--color-input-border);
      border-radius: 4px;
      font-size: 0.9375rem;
      background: var(--color-white);
      color: var(--color-black);
      transition: border-color 0.2s;
      box-sizing: border-box;
    }

    .input:focus {
      outline: none;
      border-color: var(--color-dark-gray);
    }

    .btn {
      padding: 0.625rem 1.25rem;
      border: none;
      border-radius: 4px;
      font-size: 0.9375rem;
      font-weight: 500;
      cursor: pointer;
      transition: background-color 0.2s;
    }

    .btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .btn-secondary {
      background: var(--color-surface-hover);
      color: var(--color-black);
      border: 1px solid var(--color-input-border);
    }

    .btn-secondary:hover:not(:disabled) {
      background: var(--color-surface-hover-strong);
    }

    .result {
      margin-top: 1rem;
      padding: 1rem;
      border-radius: 4px;
      font-size: 0.9375rem;
    }

    .result pre {
      margin: 0.75rem 0 0;
      padding: 0.75rem;
      background: rgba(128, 128, 128, 0.1);
      border-radius: 4px;
      overflow-x: auto;
      font-size: 0.8125rem;
    }

    .result.success {
      background: var(--color-success-bg);
      border: 1px solid var(--color-success-border);
      color: var(--color-success-text);
    }

    .result.error {
      background: var(--color-error-bg);
      border: 1px solid var(--color-error-border);
      color: var(--color-error-text);
    }

    .result.info {
      background: var(--color-info-bg);
      border: 1px solid var(--color-info-border);
      color: var(--color-info-text);
    }

    .result.pending {
      background: var(--color-warning-bg);
      border: 1px solid var(--color-warning-border);
    }

    .status-header {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .status-label {
      font-weight: 500;
      color: var(--color-dark-gray);
    }

    .status-badge {
      padding: 0.25rem 0.75rem;
      border-radius: 12px;
      font-size: 0.8125rem;
      font-weight: 600;
      text-transform: uppercase;
    }

    .status-badge.success {
      background: var(--color-success);
      color: #fff;
    }

    .status-badge.failure {
      background: var(--color-error);
      color: #fff;
    }

    .status-badge.pending,
    .status-badge.started {
      background: var(--color-warning);
      color: #1a1a1a;
    }
  `]
})
export class StatusComponent {
  private apiService = inject(ApiService);

  initialTaskId = input<string>('');

  taskId = '';
  isLoading = signal(false);
  result = signal<TaskStatus | null>(null);
  error = signal<string | null>(null);

  constructor() {
    effect(() => {
      const id = this.initialTaskId();
      if (id) {
        this.taskId = id;
        this.checkStatus();
      }
    });
  }

  setTaskId(id: string): void {
    this.taskId = id;
    this.checkStatus();
  }

  checkStatus(): void {
    if (!this.taskId) return;

    this.isLoading.set(true);
    this.result.set(null);
    this.error.set(null);

    this.apiService.getTaskStatus(this.taskId).subscribe({
      next: (response) => {
        this.isLoading.set(false);
        this.result.set(response);
      },
      error: (err) => {
        this.isLoading.set(false);
        this.error.set(`Error: ${err.message}`);
      }
    });
  }

  getResultClass(): string {
    const status = this.result()?.status;
    if (status === 'SUCCESS') return 'success';
    if (status === 'FAILURE') return 'error';
    return 'pending';
  }
}
