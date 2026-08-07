import { Component, inject, signal, input, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { ChatResponse, Citation } from '../../models/api.models';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>3. Chat / Query Documents</h2>

      <div class="form-group">
        <textarea
          [(ngModel)]="query"
          rows="3"
          placeholder="Enter your question..."
          class="textarea"
        ></textarea>
      </div>

      <div class="form-group">
        <input
          type="text"
          [(ngModel)]="docId"
          placeholder="Document ID (optional - leave empty to search all docs)"
          class="input"
        />
      </div>

      <button
        class="btn btn-success"
        (click)="sendQuery()"
        [disabled]="!query || isLoading()"
      >
        {{ isLoading() ? 'Processing...' : 'Send Query' }}
      </button>

      @if (result()) {
        <div class="result success">
          <div class="answer-section">
            <h3>Answer</h3>
            <div class="answer-text">{{ result()!.answer }}</div>
          </div>

          @if (result()!.citations && result()!.citations!.length > 0) {
            <div class="citations-section">
              <h3>Citations</h3>
              @for (citation of result()!.citations; track $index) {
                <div class="citation">
                  <div class="citation-header">
                    <span class="source-label">Source {{ $index + 1 }}:</span>
                    <span class="source-name">{{ citation.filename || 'Unknown' }}</span>
                    @if (citation.page) {
                      <span class="page-number">(Page {{ citation.page }})</span>
                    }
                  </div>
                  @if (citation.doc_id) {
                    <div class="doc-id">Doc ID: {{ citation.doc_id }}</div>
                  }
                  <div class="citation-text">"{{ citation.text_snippet }}"</div>
                </div>
              }
            </div>
          }
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

    h3 {
      margin: 0 0 0.75rem;
      font-size: 0.9375rem;
      font-weight: 600;
      color: #333;
    }

    .form-group {
      margin-bottom: 1rem;
    }

    .input, .textarea {
      width: 100%;
      padding: 0.625rem 0.875rem;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 0.9375rem;
      font-family: inherit;
      transition: border-color 0.2s;
      box-sizing: border-box;
    }

    .textarea {
      resize: vertical;
      min-height: 80px;
    }

    .input:focus, .textarea:focus {
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

    .btn-success {
      background: #28a745;
      color: #fff;
    }

    .btn-success:hover:not(:disabled) {
      background: #218838;
    }

    .result {
      margin-top: 1rem;
      padding: 1rem;
      border-radius: 4px;
      font-size: 0.9375rem;
    }

    .result.success {
      background: #f8f9fa;
      border: 1px solid #e0e0e0;
    }

    .result.error {
      background: #f8d7da;
      border: 1px solid #f5c6cb;
      color: #721c24;
    }

    .answer-section {
      margin-bottom: 1.5rem;
    }

    .answer-text {
      line-height: 1.6;
      color: #1a1a1a;
      white-space: pre-wrap;
    }

    .citations-section {
      border-top: 1px solid #e0e0e0;
      padding-top: 1rem;
    }

    .citation {
      background: #fff;
      border: 1px solid #e0e0e0;
      border-radius: 4px;
      padding: 0.875rem;
      margin-bottom: 0.75rem;
    }

    .citation:last-child {
      margin-bottom: 0;
    }

    .citation-header {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-bottom: 0.5rem;
    }

    .source-label {
      font-weight: 600;
      color: #444;
    }

    .source-name {
      color: #1a1a1a;
    }

    .page-number {
      color: #666;
      font-size: 0.875rem;
    }

    .doc-id {
      font-size: 0.8125rem;
      color: #666;
      margin-bottom: 0.5rem;
      font-family: monospace;
    }

    .citation-text {
      font-style: italic;
      color: #555;
      line-height: 1.5;
      padding-left: 0.75rem;
      border-left: 3px solid #ddd;
    }
  `]
})
export class ChatComponent {
  private apiService = inject(ApiService);

  initialDocId = input<string>('');

  query = '';
  docId = '';
  isLoading = signal(false);
  result = signal<ChatResponse | null>(null);
  error = signal<string | null>(null);

  constructor() {
    effect(() => {
      const id = this.initialDocId();
      if (id) {
        this.docId = id;
      }
    });
  }

  setDocId(id: string): void {
    this.docId = id;
  }

  sendQuery(): void {
    if (!this.query) return;

    this.isLoading.set(true);
    this.result.set(null);
    this.error.set(null);

    const request = {
      query: this.query,
      ...(this.docId && { doc_id: this.docId })
    };

    this.apiService.chat(request).subscribe({
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
}
