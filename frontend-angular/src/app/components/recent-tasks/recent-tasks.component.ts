import { Component, inject, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-recent-tasks',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="card">
      <div class="card-header">
        <h2>Recent Uploads & Tasks</h2>
        @if (apiService.recentTasks().length > 0) {
          <button class="btn-clear" (click)="clearTasks()">Clear All</button>
        }
      </div>

      @if (apiService.recentTasks().length === 0) {
        <p class="empty-state">No recent tasks</p>
      } @else {
        <div class="tasks-list">
          @for (task of apiService.recentTasks(); track task.task_id) {
            <div class="task-item">
              <div class="task-main">
                <div class="task-info">
                  <span class="filename">{{ task.filename || 'Unknown file' }}</span>
                  <span class="task-id">Task: {{ task.task_id }}</span>
                  @if (task.doc_id) {
                    <span class="doc-id">Doc: {{ task.doc_id }}</span>
                  }
                </div>
                <span class="status-badge" [class]="task.status">
                  {{ task.status.toUpperCase() }}
                </span>
              </div>
              <div class="task-footer">
                <span class="timestamp">{{ formatDate(task.timestamp) }}</span>
                <div class="task-actions">
                  <button class="btn btn-sm btn-secondary" (click)="onCheckStatus(task.task_id)">
                    Check Status
                  </button>
                  @if (task.doc_id) {
                    <button class="btn btn-sm btn-success" (click)="onUseInChat(task.doc_id)">
                      Search this doc
                    </button>
                  }
                </div>
              </div>
            </div>
          }
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

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
    }

    h2 {
      margin: 0;
      font-size: 1.125rem;
      font-weight: 600;
      color: var(--color-black);
    }

    .btn-clear {
      background: none;
      border: none;
      color: var(--color-medium-gray);
      font-size: 0.875rem;
      cursor: pointer;
      text-decoration: underline;
    }

    .btn-clear:hover {
      color: var(--color-error);
    }

    .empty-state {
      color: var(--color-medium-gray);
      font-style: italic;
      margin: 0;
      padding: 1rem 0;
    }

    .tasks-list {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }

    .task-item {
      border: 1px solid var(--color-border);
      border-radius: 6px;
      padding: 1rem;
      background: var(--color-surface-alt);
    }

    .task-main {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
    }

    .task-info {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
    }

    .filename {
      font-weight: 600;
      color: var(--color-black);
    }

    .task-id, .doc-id {
      font-size: 0.8125rem;
      color: var(--color-medium-gray);
      font-family: monospace;
    }

    .status-badge {
      padding: 0.25rem 0.625rem;
      border-radius: 12px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      flex-shrink: 0;
    }

    .status-badge.success {
      background: var(--color-success-bg);
      color: var(--color-success-text);
    }

    .status-badge.failure {
      background: var(--color-error-bg);
      color: var(--color-error-text);
    }

    .status-badge.pending,
    .status-badge.started {
      background: var(--color-warning-bg);
      color: var(--color-warning-text);
    }

    .task-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 0.75rem;
      padding-top: 0.75rem;
      border-top: 1px solid var(--color-border);
    }

    .timestamp {
      font-size: 0.8125rem;
      color: var(--color-light-gray);
    }

    .task-actions {
      display: flex;
      gap: 0.5rem;
    }

    .btn {
      padding: 0.5rem 0.875rem;
      border: none;
      border-radius: 4px;
      font-size: 0.8125rem;
      font-weight: 500;
      cursor: pointer;
      transition: background-color 0.2s;
    }

    .btn-sm {
      padding: 0.375rem 0.75rem;
    }

    .btn-secondary {
      background: var(--color-surface-hover);
      color: var(--color-black);
      border: 1px solid var(--color-input-border);
    }

    .btn-secondary:hover {
      background: var(--color-surface-hover-strong);
    }

    .btn-success {
      background: var(--color-success);
      color: #fff;
    }

    .btn-success:hover {
      background: var(--color-success);
      filter: brightness(0.9);
    }

    @media (max-width: 480px) {
      .task-main {
        flex-direction: column;
      }

      .task-footer {
        flex-direction: column;
        align-items: flex-start;
        gap: 0.75rem;
      }
    }
  `]
})
export class RecentTasksComponent {
  apiService = inject(ApiService);

  checkStatus = output<string>();
  useInChat = output<string>();

  formatDate(timestamp: string): string {
    return new Date(timestamp).toLocaleString();
  }

  onCheckStatus(taskId: string): void {
    this.checkStatus.emit(taskId);
  }

  onUseInChat(docId: string): void {
    this.useInChat.emit(docId);
  }

  clearTasks(): void {
    if (confirm('Clear all recent tasks?')) {
      this.apiService.clearRecentTasks();
    }
  }
}
