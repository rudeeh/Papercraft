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
      <h2>2. Check Task Status</h2>

      <div class="form-group">
        <input
          type="text"
          [(ngModel)]="taskId"
          placeholder="Enter task ID from upload"
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
      background: #fff;
      border: 1px solid #e0e0e0;
      border-radius: 8px;
      padding: 1.5rem;
    }

    h2 {
      margin: 0 0 1.25rem;
      font-size: 1.125rem;
      font-weight: 600;
      color: #1a1a1a;
    }

    .form-group {
      margin-bottom: 1rem;
    }

    .input {
      width: 100%;
      padding: 0.625rem 0.875rem;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 0.9375rem;
      transition: border-color 0.2s;
      box-sizing: border-box;
    }

    .input:focus {
      outline: none;
      border-color: #333;
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
      background: #f5f5f5;
      color: #1a1a1a;
      border: 1px solid #ddd;
    }

    .btn-secondary:hover:not(:disabled) {
      background: #e8e8e8;
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
      background: rgba(0, 0, 0, 0.05);
      border-radius: 4px;
      overflow-x: auto;
      font-size: 0.8125rem;
    }

    .result.success {
      background: #d4edda;
      border: 1px solid #c3e6cb;
    }

    .result.error {
      background: #f8d7da;
      border: 1px solid #f5c6cb;
      color: #721c24;
    }

    .result.info {
      background: #e7f3ff;
      border: 1px solid #b8daff;
    }

    .result.pending {
      background: #fff3cd;
      border: 1px solid #ffc107;
    }

    .status-header {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .status-label {
      font-weight: 500;
      color: #444;
    }

    .status-badge {
      padding: 0.25rem 0.75rem;
      border-radius: 12px;
      font-size: 0.8125rem;
      font-weight: 600;
      text-transform: uppercase;
    }

    .status-badge.success {
      background: #28a745;
      color: #fff;
    }

    .status-badge.failure {
      background: #dc3545;
      color: #fff;
    }

    .status-badge.pending,
    .status-badge.started {
      background: #ffc107;
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
