import { Component, inject, signal, computed, input, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { PaperGraphResponse, PaperGraphNode } from '../../models/api.models';

interface PositionedNode extends PaperGraphNode {
  x: number;
  y: number;
  color: string;
}

const TYPE_COLORS: Record<string, string> = {
  Paper: '#1a1a1a',
  Method: '#2b6cb0',
  Dataset: '#2f855a',
  Task: '#b7791f',
  Metric: '#805ad5',
  Author: '#718096',
  Institution: '#a0aec0',
  Claim: '#c53030',
  Experiment: '#d69e2e',
  Section: '#4a5568',
};

/**
 * Basic (non-animated) paper graph view: nodes laid out on a static circle,
 * edges drawn as straight lines. Deliberately simple per Phase 17 --
 * no force simulation, dragging, or zoom.
 */
@Component({
  selector: 'app-paper-graph',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h2>5. Paper Graph View</h2>

      <div class="form-group">
        <input
          type="text"
          [(ngModel)]="paperId"
          placeholder="Paper ID (e.g. click a source paper above, or use an upload doc_id)"
          class="input"
        />
      </div>

      <button
        class="btn btn-secondary"
        (click)="load()"
        [disabled]="!paperId || isLoading()"
      >
        {{ isLoading() ? 'Loading...' : 'Load Graph' }}
      </button>

      @if (graph() && positionedNodes().length > 0) {
        <div class="graph-wrap">
          <svg viewBox="0 0 400 400" class="graph-svg">
            @for (edge of graph()!.edges; track $index) {
              @if (nodePos(edge.source) && nodePos(edge.target)) {
                <line
                  [attr.x1]="nodePos(edge.source)!.x"
                  [attr.y1]="nodePos(edge.source)!.y"
                  [attr.x2]="nodePos(edge.target)!.x"
                  [attr.y2]="nodePos(edge.target)!.y"
                  class="edge-line"
                />
              }
            }
            @for (node of positionedNodes(); track node.id) {
              <circle [attr.cx]="node.x" [attr.cy]="node.y" r="9" [attr.fill]="node.color" />
              <text [attr.x]="node.x" [attr.y]="node.y - 13" class="node-label">{{ shortLabel(node) }}</text>
            }
          </svg>
        </div>

        <div class="legend">
          @for (type of nodeTypesPresent(); track type) {
            <span class="legend-item">
              <span class="legend-dot" [style.background]="colorFor(type)"></span>{{ type }}
            </span>
          }
        </div>

        @if (graph()!.edges.length > 0) {
          <div class="edge-list">
            <h3>Relationships</h3>
            @for (edge of graph()!.edges; track $index) {
              <div class="edge-row">
                {{ labelFor(edge.source) }} <span class="rel">{{ edge.type }}</span> {{ labelFor(edge.target) }}
              </div>
            }
          </div>
        }
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

    .input {
      width: 100%;
      padding: 0.625rem 0.875rem;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 0.9375rem;
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

    .graph-wrap {
      margin-top: 1rem;
      display: flex;
      justify-content: center;
      background: #fafafa;
      border: 1px solid #e0e0e0;
      border-radius: 4px;
    }

    .graph-svg {
      width: 100%;
      max-width: 400px;
      height: auto;
    }

    .edge-line {
      stroke: #ccc;
      stroke-width: 1.5;
    }

    .node-label {
      font-size: 8px;
      text-anchor: middle;
      fill: #333;
    }

    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 0.75rem;
      font-size: 0.75rem;
      color: #555;
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
    }

    .edge-list {
      border-top: 1px solid #e0e0e0;
      margin-top: 1rem;
      padding-top: 1rem;
    }

    .edge-row {
      font-size: 0.8125rem;
      color: #444;
      margin-bottom: 0.375rem;
    }

    .rel {
      color: #666;
      font-family: monospace;
      font-size: 0.75rem;
      background: #f0f0f0;
      border-radius: 3px;
      padding: 0.1rem 0.4rem;
      margin: 0 0.25rem;
    }

    .result {
      margin-top: 1rem;
      padding: 1rem;
      border-radius: 4px;
      font-size: 0.9375rem;
    }

    .result.error {
      background: #f8d7da;
      border: 1px solid #f5c6cb;
      color: #721c24;
    }
  `]
})
export class PaperGraphComponent {
  private apiService = inject(ApiService);

  initialPaperId = input<string>('');

  paperId = '';
  isLoading = signal(false);
  graph = signal<PaperGraphResponse | null>(null);
  error = signal<string | null>(null);

  positionedNodes = computed<PositionedNode[]>(() => {
    const g = this.graph();
    if (!g || g.nodes.length === 0) return [];

    const cx = 200;
    const cy = 200;
    const r = 150;
    const n = g.nodes.length;

    return g.nodes.map((node, i) => {
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      return {
        ...node,
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle),
        color: this.colorFor(node.type)
      };
    });
  });

  constructor() {
    effect(() => {
      const id = this.initialPaperId();
      if (id) {
        this.setPaperId(id);
      }
    });
  }

  setPaperId(id: string): void {
    this.paperId = id;
    this.load();
  }

  load(): void {
    if (!this.paperId) return;

    this.isLoading.set(true);
    this.graph.set(null);
    this.error.set(null);

    this.apiService.getPaperGraph(this.paperId).subscribe({
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

  nodePos(id: string): { x: number; y: number } | undefined {
    return this.positionedNodes().find(n => n.id === id);
  }

  labelFor(id: string): string {
    const node = this.graph()?.nodes.find(n => n.id === id);
    return node ? this.shortLabel(node) : id;
  }

  shortLabel(node: PaperGraphNode): string {
    const name = (node['name'] as string) || (node['title'] as string) || node.id;
    return name.length > 20 ? name.slice(0, 18) + '…' : name;
  }

  nodeTypesPresent(): string[] {
    const g = this.graph();
    if (!g) return [];
    return Array.from(new Set(g.nodes.map(n => n.type)));
  }

  colorFor(type: string): string {
    return TYPE_COLORS[type] || '#999';
  }
}
