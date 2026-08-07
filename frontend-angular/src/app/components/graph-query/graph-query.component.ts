import { Component, inject, signal, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { GraphQueryResponse } from '../../models/api.models';

@Component({
  selector: 'app-graph-query',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>4. Ask a Graph Question</h2>
      <p class="hint">
        Questions are routed to graph traversal, semantic search, or both,
        depending on what you're asking (e.g. citations, comparisons,
        evolution of a method, or a plain explanation).
      </p>

      <div class="form-group">
        <textarea
          [(ngModel)]="query"
          rows="3"
          placeholder="e.g. Which methods improved upon the Transformer?"
          class="textarea"
        ></textarea>
      </div>

      <button
        class="btn btn-primary"
        (click)="ask()"
        [disabled]="!query || isLoading()"
      >
        {{ isLoading() ? 'Thinking...' : 'Ask' }}
      </button>

      @if (result()) {
        <div class="result success">
          <div class="answer-section">
            <div class="section-header">
              <h3>Answer</h3>
              <span class="query-type-badge">{{ result()!.retrieval_trace.query_type }}</span>
            </div>
            <div class="answer-text">{{ result()!.answer }}</div>
          </div>

          @if (result()!.retrieval_trace.confidence_notes.length > 0) {
            <div class="notes-section">
              @for (note of result()!.retrieval_trace.confidence_notes; track $index) {
                <div class="confidence-note">&#9888; {{ note }}</div>
              }
            </div>
          }

          @if (result()!.sources.length > 0) {
            <div class="subsection">
              <h3>Source Papers</h3>
              @for (source of result()!.sources; track source.paper_id) {
                <div class="chip" (click)="viewGraph(source.paper_id)">
                  {{ source.title || source.paper_id }}
                </div>
              }
            </div>
          }

          @if (result()!.retrieval_trace.graph_facts.length > 0) {
            <div class="subsection">
              <h3>Graph Facts Used</h3>
              @for (fact of result()!.retrieval_trace.graph_facts; track $index) {
                <div class="fact">
                  <div class="fact-triple">
                    <span class="entity">{{ fact.subject.name }}</span>
                    <span class="relation">{{ fact.relation }}</span>
                    <span class="entity">{{ fact.object.name }}</span>
                  </div>
                  @if (fact.evidence) {
                    <div class="fact-evidence">"{{ fact.evidence }}"</div>
                  }
                </div>
              }
            </div>
          }

          @if (result()!.retrieval_trace.citation_paths.length > 0) {
            <div class="subsection">
              <h3>Citation Path</h3>
              @for (cp of result()!.retrieval_trace.citation_paths; track $index) {
                <div class="citation-path">
                  {{ cp.path.join(' → ') }}
                  <span class="path-meta">(depth {{ cp.depth }}, {{ cp.direction }})</span>
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
      margin: 0 0 0.5rem;
      font-size: 1.125rem;
      font-weight: 600;
      color: #1a1a1a;
    }

    .hint {
      margin: 0 0 1rem;
      color: #666;
      font-size: 0.8125rem;
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

    .textarea {
      width: 100%;
      padding: 0.625rem 0.875rem;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 0.9375rem;
      font-family: inherit;
      resize: vertical;
      min-height: 80px;
      box-sizing: border-box;
    }

    .textarea:focus {
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

    .btn-primary {
      background: #1a1a1a;
      color: #fff;
    }

    .btn-primary:hover:not(:disabled) {
      background: #333;
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
      margin-bottom: 1rem;
    }

    .section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
    }

    .query-type-badge {
      background: #e7f3ff;
      color: #004085;
      border-radius: 12px;
      padding: 0.15rem 0.625rem;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }

    .answer-text {
      line-height: 1.6;
      color: #1a1a1a;
      white-space: pre-wrap;
    }

    .notes-section {
      margin-bottom: 1rem;
    }

    .confidence-note {
      background: #fff3cd;
      border: 1px solid #ffe69c;
      border-radius: 4px;
      padding: 0.5rem 0.75rem;
      font-size: 0.8125rem;
      color: #664d03;
      margin-bottom: 0.5rem;
    }

    .subsection {
      border-top: 1px solid #e0e0e0;
      padding-top: 1rem;
      margin-top: 1rem;
    }

    .chip {
      display: inline-block;
      background: #fff;
      border: 1px solid #ccc;
      border-radius: 16px;
      padding: 0.25rem 0.75rem;
      font-size: 0.8125rem;
      margin: 0 0.5rem 0.5rem 0;
      cursor: pointer;
    }

    .chip:hover {
      border-color: #1a1a1a;
      background: #f5f5f5;
    }

    .fact {
      background: #fff;
      border: 1px solid #e0e0e0;
      border-radius: 4px;
      padding: 0.75rem;
      margin-bottom: 0.5rem;
    }

    .fact:last-child {
      margin-bottom: 0;
    }

    .fact-triple {
      font-size: 0.875rem;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      flex-wrap: wrap;
    }

    .entity {
      font-weight: 600;
      color: #1a1a1a;
    }

    .relation {
      color: #666;
      font-family: monospace;
      font-size: 0.75rem;
      background: #f0f0f0;
      border-radius: 3px;
      padding: 0.1rem 0.4rem;
    }

    .fact-evidence {
      margin-top: 0.375rem;
      font-style: italic;
      color: #555;
      font-size: 0.8125rem;
      padding-left: 0.75rem;
      border-left: 3px solid #ddd;
    }

    .citation-path {
      font-size: 0.875rem;
      color: #1a1a1a;
      margin-bottom: 0.375rem;
      font-family: monospace;
    }

    .path-meta {
      color: #888;
      font-family: inherit;
      font-size: 0.75rem;
      margin-left: 0.375rem;
    }
  `]
})
export class GraphQueryComponent {
  private apiService = inject(ApiService);

  viewPaperGraph = output<string>();

  query = '';
  isLoading = signal(false);
  result = signal<GraphQueryResponse | null>(null);
  error = signal<string | null>(null);

  ask(): void {
    if (!this.query) return;

    this.isLoading.set(true);
    this.result.set(null);
    this.error.set(null);

    this.apiService.graphQuery({ query: this.query, top_k: 10 }).subscribe({
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

  viewGraph(paperId: string): void {
    this.viewPaperGraph.emit(paperId);
  }
}
