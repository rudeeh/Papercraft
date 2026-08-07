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
                      Use in Chat
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
      background: #fff;
      border: 1px solid #e0e0e0;
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
      color: #1a1a1a;
    }

    .btn-clear {
      background: none;
      border: none;
      color: #666;
      font-size: 0.875rem;
      cursor: pointer;
      text-decoration: underline;
    }

    .btn-clear:hover {
      color: #dc3545;
    }

    .empty-state {
      color: #666;
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
      border: 1px solid #e0e0e0;
      border-radius: 6px;
      padding: 1rem;
      background: #fafafa;
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
      color: #1a1a1a;
    }

    .task-id, .doc-id {
      font-size: 0.8125rem;
      color: #666;
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
      background: #d4edda;
      color: #155724;
    }

    .status-badge.failure {
      background: #f8d7da;
      color: #721c24;
    }

    .status-badge.pending,
    .status-badge.started {
      background: #fff3cd;
      color: #856404;
    }

    .task-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 0.75rem;
      padding-top: 0.75rem;
      border-top: 1px solid #e8e8e8;
    }

    .timestamp {
      font-size: 0.8125rem;
      color: #888;
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
      background: #f0f0f0;
      color: #1a1a1a;
      border: 1px solid #ddd;
    }

    .btn-secondary:hover {
      background: #e0e0e0;
    }

    .btn-success {
      background: #28a745;
      color: #fff;
    }

    .btn-success:hover {
      background: #218838;
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
