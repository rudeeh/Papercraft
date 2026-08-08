import { Component, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { CitationGraphResponse, CitationGraphPaper } from '../../models/api.models';

interface PositionedPaper extends CitationGraphPaper {
  x: number;
  y: number;
}

/**
 * Global citation graph explorer: every ingested paper (real or stub) and
 * every CITES edge between them, from our own Neo4j data -- replaces the
 * old standalone graph_visualizer.html (which only knew about the external
 * Semantic Scholar API, not anything actually uploaded here).
 *
 * Deliberately simple: a static circle layout, no force simulation, no
 * zoom/pan, no animations. Click a paper to inspect it.
 */
@Component({
  selector: 'app-citation-explorer',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>Explore the graph</h2>
      <p class="hint">Every ingested paper and the citation links between them. Click one to inspect it.</p>

      <div class="toolbar">
        <input
          type="text"
          [(ngModel)]="searchTerm"
          placeholder="Filter by title..."
          class="input"
        />
        <button class="btn btn-secondary" (click)="load()" [disabled]="isLoading()">
          {{ isLoading() ? 'Loading...' : 'Refresh' }}
        </button>
      </div>

      @if (graph() && positionedPapers().length > 0) {
        <div class="graph-wrap">
          <svg viewBox="0 0 440 440" class="graph-svg">
            @for (edge of graph()!.edges; track $index) {
              @if (posOf(edge.source) && posOf(edge.target)) {
                <line
                  [attr.x1]="posOf(edge.source)!.x"
                  [attr.y1]="posOf(edge.source)!.y"
                  [attr.x2]="posOf(edge.target)!.x"
                  [attr.y2]="posOf(edge.target)!.y"
                  class="edge-line"
                />
              }
            }
            @for (paper of positionedPapers(); track paper.paper_id) {
              <circle
                [attr.cx]="paper.x"
                [attr.cy]="paper.y"
                [attr.r]="paper.paper_id === selected()?.paper_id ? 10 : 7"
                [class.stub]="paper.is_stub"
                [class.matched]="isMatch(paper)"
                [class.dimmed]="searchTerm && !isMatch(paper)"
                [class.selected]="paper.paper_id === selected()?.paper_id"
                class="paper-dot"
                (click)="select(paper)"
              />
            }
          </svg>
        </div>

        <div class="legend">
          <span class="legend-item"><span class="legend-dot real"></span>Ingested paper</span>
          <span class="legend-item"><span class="legend-dot stub"></span>Cited, not yet ingested</span>
        </div>

        @if (selected()) {
          <div class="info-panel">
            <div class="info-title">{{ selected()!.title || selected()!.name || selected()!.paper_id }}</div>
            <div class="info-meta">
              paper_id: {{ selected()!.paper_id }}
              @if (selected()!.year) { · {{ selected()!.year }} }
              · {{ selected()!.is_stub ? 'not yet ingested' : 'ingested' }}
            </div>
          </div>
        }

        <div class="paper-list">
          @for (paper of filteredPapers(); track paper.paper_id) {
            <div
              class="paper-row"
              [class.selected]="paper.paper_id === selected()?.paper_id"
              (click)="select(paper)"
            >
              <span class="dot" [class.stub]="paper.is_stub"></span>
              {{ paper.title || paper.name || paper.paper_id }}
            </div>
          }
        </div>
      } @else if (!isLoading()) {
        <p class="empty">No papers ingested yet.</p>
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
      margin: 0 0 1rem;
      color: var(--color-medium-gray);
      font-size: 0.8125rem;
    }

    .toolbar {
      display: flex;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }

    .input {
      flex: 1;
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--color-input-border);
      border-radius: 4px;
      font-size: 0.875rem;
      background: var(--color-white);
      color: var(--color-black);
    }

    .input:focus {
      outline: none;
      border-color: var(--color-dark-gray);
    }

    .btn {
      padding: 0.5rem 1rem;
      border: none;
      border-radius: 4px;
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      white-space: nowrap;
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

    .graph-wrap {
      display: flex;
      justify-content: center;
      background: var(--color-surface-alt);
      border: 1px solid var(--color-border);
      border-radius: 4px;
    }

    .graph-svg {
      width: 100%;
      max-width: 400px;
      height: auto;
    }

    .edge-line {
      stroke: var(--graph-edge-color);
      stroke-width: 1;
    }

    .paper-dot {
      fill: var(--color-black);
      cursor: pointer;
    }

    .paper-dot.stub {
      fill: var(--color-surface-alt);
      stroke: var(--color-light-gray);
      stroke-width: 1.5;
      stroke-dasharray: 2 2;
    }

    .paper-dot.matched {
      fill: #0d6efd;
    }

    .paper-dot.dimmed {
      opacity: 0.25;
    }

    .paper-dot.selected {
      stroke: #0d6efd;
      stroke-width: 2;
    }

    .legend {
      display: flex;
      gap: 1rem;
      margin-top: 0.75rem;
      font-size: 0.75rem;
      color: var(--color-medium-gray);
    }

    .legend-item {
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }

    .legend-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      display: inline-block;
      background: var(--color-black);
    }

    .legend-dot.stub {
      background: var(--color-white);
      border: 1.5px dashed var(--color-light-gray);
    }

    .info-panel {
      margin-top: 1rem;
      padding: 0.875rem;
      background: var(--color-surface-alt);
      border: 1px solid var(--color-border);
      border-radius: 4px;
    }

    .info-title {
      font-weight: 600;
      color: var(--color-black);
      margin-bottom: 0.25rem;
    }

    .info-meta {
      font-size: 0.75rem;
      color: var(--color-medium-gray);
      font-family: monospace;
      margin-bottom: 0.625rem;
      word-break: break-all;
    }

    .paper-list {
      margin-top: 1rem;
      max-height: 180px;
      overflow-y: auto;
      border-top: 1px solid var(--color-border);
      padding-top: 0.5rem;
    }

    .paper-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.375rem 0.25rem;
      font-size: 0.8125rem;
      color: var(--color-dark-gray);
      cursor: pointer;
      border-radius: 4px;
    }

    .paper-row:hover {
      background: var(--color-surface-hover);
    }

    .paper-row.selected {
      background: var(--color-info-bg);
    }

    .paper-row .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--color-black);
      flex-shrink: 0;
    }

    .paper-row .dot.stub {
      background: var(--color-white);
      border: 1.5px dashed var(--color-light-gray);
    }

    .empty {
      color: var(--color-medium-gray);
      font-size: 0.875rem;
    }

    .result.error {
      margin-top: 1rem;
      padding: 1rem;
      border-radius: 4px;
      background: var(--color-error-bg);
      border: 1px solid var(--color-error-border);
      color: var(--color-error-text);
      font-size: 0.875rem;
    }
  `]
})
export class CitationExplorerComponent {
  private apiService = inject(ApiService);

  searchTerm = '';
  isLoading = signal(false);
  graph = signal<CitationGraphResponse | null>(null);
  error = signal<string | null>(null);
  selected = signal<CitationGraphPaper | null>(null);

  positionedPapers = computed<PositionedPaper[]>(() => {
    const g = this.graph();
    if (!g || g.papers.length === 0) return [];

    const cx = 220;
    const cy = 220;
    const r = 170;
    const n = g.papers.length;

    return g.papers.map((paper, i) => {
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      return { ...paper, x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
    });
  });

  filteredPapers = computed<CitationGraphPaper[]>(() => {
    const g = this.graph();
    if (!g) return [];
    if (!this.searchTerm.trim()) return g.papers;
    const term = this.searchTerm.trim().toLowerCase();
    return g.papers.filter(p => (p.title || p.name || p.paper_id).toLowerCase().includes(term));
  });

  constructor() {
    this.load();
  }

  load(): void {
    this.isLoading.set(true);
    this.error.set(null);
    this.selected.set(null);

    this.apiService.getCitationGraph().subscribe({
      next: (response) => {
        this.isLoading.set(false);
        this.graph.set(response);
      },
      error: (err) => {
        this.isLoading.set(false);
        this.error.set(`Error: ${err.message}`);
      }
    });
  }

  posOf(paperId: string): { x: number; y: number } | undefined {
    return this.positionedPapers().find(p => p.paper_id === paperId);
  }

  isMatch(paper: CitationGraphPaper): boolean {
    if (!this.searchTerm.trim()) return false;
    const term = this.searchTerm.trim().toLowerCase();
    return (paper.title || paper.name || paper.paper_id).toLowerCase().includes(term);
  }

  select(paper: CitationGraphPaper): void {
    this.selected.set(paper);
  }
}
