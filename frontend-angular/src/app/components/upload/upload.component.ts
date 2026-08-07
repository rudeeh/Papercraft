import { Component, inject, signal, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { UploadResponse } from '../../models/api.models';

@Component({
  selector: 'app-upload',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>1. Upload PDF</h2>

      <div class="form-group">
        <div
          class="file-drop-zone"
          [class.dragover]="isDragover()"
          [class.has-file]="selectedFile()"
          (dragover)="onDragOver($event)"
          (dragleave)="onDragLeave()"
          (drop)="onDrop($event)"
          (click)="fileInput.click()"
        >
          @if (selectedFile()) {
            <div class="file-info">
              <span class="file-icon">&#128196;</span>
              <span class="file-name">{{ selectedFile()?.name }}</span>
              <button class="remove-btn" (click)="removeFile($event)">&#10005;</button>
            </div>
          } @else {
            <span class="drop-text">Drop PDF here or click to browse</span>
          }
        </div>
        <input
          #fileInput
          type="file"
          accept=".pdf"
          (change)="onFileSelect($event)"
          hidden
        />
      </div>

      <div class="form-group checkbox-group">
        <label>
          <input type="checkbox" [(ngModel)]="forceUpload" />
          <span>Force re-process if file exists</span>
        </label>
      </div>

      <button
        class="btn btn-primary"
        (click)="upload()"
        [disabled]="!selectedFile() || isLoading()"
      >
        {{ isLoading() ? 'Uploading...' : 'Upload PDF' }}
      </button>

      @if (result()) {
        <div class="result" [class]="result()!.type">
          <p>{{ result()!.message }}</p>
          @if (result()!.data) {
            <pre>{{ result()!.data | json }}</pre>
          }
          @if (result()!.taskId) {
            <button class="btn btn-secondary btn-sm" (click)="checkTaskStatus()">
              Check Status
            </button>
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

    h2 {
      margin: 0 0 1.25rem;
      font-size: 1.125rem;
      font-weight: 600;
      color: #1a1a1a;
    }

    .form-group {
      margin-bottom: 1rem;
    }

    .file-drop-zone {
      border: 2px dashed #ccc;
      border-radius: 8px;
      padding: 2rem;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;
      background: #fafafa;
    }

    .file-drop-zone:hover,
    .file-drop-zone.dragover {
      border-color: #666;
      background: #f0f0f0;
    }

    .file-drop-zone.has-file {
      border-style: solid;
      border-color: #28a745;
      background: #f8fff8;
    }

    .drop-text {
      color: #666;
      font-size: 0.9375rem;
    }

    .file-info {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.75rem;
    }

    .file-icon {
      font-size: 1.5rem;
    }

    .file-name {
      font-weight: 500;
      color: #1a1a1a;
    }

    .remove-btn {
      background: none;
      border: none;
      color: #999;
      cursor: pointer;
      font-size: 1rem;
      padding: 0.25rem;
      line-height: 1;
    }

    .remove-btn:hover {
      color: #dc3545;
    }

    .checkbox-group label {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      cursor: pointer;
      font-size: 0.9375rem;
      color: #444;
    }

    .checkbox-group input[type="checkbox"] {
      width: 16px;
      height: 16px;
      cursor: pointer;
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

    .btn-primary {
      background: #1a1a1a;
      color: #fff;
    }

    .btn-primary:hover:not(:disabled) {
      background: #333;
    }

    .btn-secondary {
      background: #f5f5f5;
      color: #1a1a1a;
      border: 1px solid #ddd;
    }

    .btn-secondary:hover:not(:disabled) {
      background: #e8e8e8;
    }

    .btn-sm {
      padding: 0.5rem 1rem;
      font-size: 0.875rem;
    }

    .result {
      margin-top: 1rem;
      padding: 1rem;
      border-radius: 4px;
      font-size: 0.9375rem;
    }

    .result p {
      margin: 0 0 0.5rem;
    }

    .result pre {
      margin: 0.5rem 0;
      padding: 0.75rem;
      background: rgba(0, 0, 0, 0.05);
      border-radius: 4px;
      overflow-x: auto;
      font-size: 0.8125rem;
    }

    .result.success {
      background: #d4edda;
      border: 1px solid #c3e6cb;
      color: #155724;
    }

    .result.error {
      background: #f8d7da;
      border: 1px solid #f5c6cb;
      color: #721c24;
    }

    .result.info {
      background: #e7f3ff;
      border: 1px solid #b8daff;
      color: #004085;
    }
  `]
})
export class UploadComponent {
  private apiService = inject(ApiService);

  taskUploaded = output<{ taskId: string; docId?: string }>();

  selectedFile = signal<File | null>(null);
  isDragover = signal(false);
  forceUpload = false;
  isLoading = signal(false);
  result = signal<{ message: string; type: string; data?: UploadResponse; taskId?: string } | null>(null);

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragover.set(true);
  }

  onDragLeave(): void {
    this.isDragover.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragover.set(false);

    const files = event.dataTransfer?.files;
    if (files && files.length > 0) {
      const file = files[0];
      if (file.name.toLowerCase().endsWith('.pdf')) {
        this.selectedFile.set(file);
        this.result.set(null);
      } else {
        this.result.set({ message: 'Please select a valid PDF file', type: 'error' });
      }
    }
  }

  onFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      const file = input.files[0];
      if (file.name.toLowerCase().endsWith('.pdf')) {
        this.selectedFile.set(file);
        this.result.set(null);
      } else {
        this.result.set({ message: 'Please select a valid PDF file', type: 'error' });
      }
    }
  }

  removeFile(event: Event): void {
    event.stopPropagation();
    this.selectedFile.set(null);
    this.result.set(null);
  }

  upload(): void {
    const file = this.selectedFile();
    if (!file) return;

    this.isLoading.set(true);
    this.result.set({ message: 'Uploading...', type: 'info' });

    this.apiService.uploadPdf(file, this.forceUpload).subscribe({
      next: (response) => {
        this.isLoading.set(false);
        this.result.set({
          message: response.message,
          type: 'success',
          data: response,
          taskId: response.task_id
        });

        if (response.task_id) {
          this.taskUploaded.emit({ taskId: response.task_id, docId: response.doc_id });
        }
      },
      error: (error) => {
        this.isLoading.set(false);
        this.result.set({ message: `Error: ${error.message}`, type: 'error' });
      }
    });
  }

  checkTaskStatus(): void {
    const taskId = this.result()?.taskId;
    if (taskId) {
      this.taskUploaded.emit({ taskId, docId: this.result()?.data?.doc_id });
    }
  }
}
