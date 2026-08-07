import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-header',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <header class="header">
      <div class="header-content">
        <h1>DocRAG</h1>
        <p class="subtitle">Document Retrieval-Augmented Generation</p>
      </div>
      <div class="header-controls">
        <div class="api-url-input">
          <label for="apiUrl">API URL:</label>
          <input
            type="text"
            id="apiUrl"
            [ngModel]="apiService.apiUrl()"
            (ngModelChange)="onApiUrlChange($event)"
            placeholder="https://docrag-2gvg.onrender.com"
          />
        </div>
        <div class="status-indicator" [class.online]="apiService.healthStatus().online">
          <span class="dot"></span>
          <span class="text">{{ apiService.healthStatus().online ? 'Online' : 'Offline' }}</span>
        </div>
      </div>
    </header>
  `,
  styles: [`
    .header {
      background: #fff;
      border-bottom: 1px solid #e0e0e0;
      padding: 1.5rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }

    .header-content h1 {
      margin: 0;
      font-size: 1.75rem;
      font-weight: 600;
      color: #1a1a1a;
    }

    .subtitle {
      margin: 0.25rem 0 0;
      font-size: 0.875rem;
      color: #666;
    }

    .header-controls {
      display: flex;
      align-items: center;
      gap: 1.5rem;
      flex-wrap: wrap;
    }

    .api-url-input {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .api-url-input label {
      font-size: 0.875rem;
      color: #444;
      font-weight: 500;
    }

    .api-url-input input {
      padding: 0.5rem 0.75rem;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 0.875rem;
      width: 280px;
      transition: border-color 0.2s;
    }

    .api-url-input input:focus {
      outline: none;
      border-color: #333;
    }

    .status-indicator {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      background: #f5f5f5;
      border-radius: 20px;
      font-size: 0.875rem;
    }

    .status-indicator .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #dc3545;
    }

    .status-indicator.online .dot {
      background: #28a745;
    }

    .status-indicator .text {
      color: #444;
      font-weight: 500;
    }

    @media (max-width: 768px) {
      .header {
        padding: 1rem;
      }

      .api-url-input input {
        width: 200px;
      }
    }
  `]
})
export class HeaderComponent {
  apiService = inject(ApiService);

  onApiUrlChange(url: string): void {
    this.apiService.setApiUrl(url);
  }
}
