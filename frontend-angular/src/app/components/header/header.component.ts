import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { ThemeService } from '../../services/theme.service';

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
            placeholder="http://localhost:8000"
          />
        </div>
        <div class="api-url-input llm-key-input" [class.required]="!apiService.llmStatus().server_key_configured">
          <label for="llmApiKey">
            OpenRouter Key{{ apiService.llmStatus().server_key_configured ? ' (optional):' : ' (required):' }}
          </label>
          <input
            type="password"
            id="llmApiKey"
            [ngModel]="apiService.llmApiKey()"
            (ngModelChange)="onLlmApiKeyChange($event)"
            placeholder="sk-or-v1-..."
          />
        </div>
        <div class="status-indicator" [class.online]="apiService.healthStatus().online">
          <span class="dot"></span>
          <span class="text">{{ apiService.healthStatus().online ? 'Online' : 'Offline' }}</span>
        </div>
        <button
          type="button"
          class="theme-toggle"
          (click)="themeService.toggle()"
          [attr.aria-label]="themeService.theme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
        >
          {{ themeService.theme() === 'dark' ? '☀️ Light' : '\u{1F319} Dark' }}
        </button>
      </div>
    </header>
    @if (!apiService.llmStatus().server_key_configured && !apiService.llmApiKey()) {
      <div class="llm-key-banner">
        No server-side OpenRouter API key is configured. Enter your own key above to ask questions
        (get one free at <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>).
      </div>
    }
  `,
  styles: [`
    .header {
      background: var(--color-white);
      border-bottom: 1px solid var(--color-border);
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
      color: var(--color-black);
    }

    .subtitle {
      margin: 0.25rem 0 0;
      font-size: 0.875rem;
      color: var(--color-medium-gray);
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
      color: var(--color-dark-gray);
      font-weight: 500;
    }

    .api-url-input input {
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--color-input-border);
      border-radius: 4px;
      font-size: 0.875rem;
      width: 280px;
      background: var(--color-white);
      color: var(--color-black);
      transition: border-color 0.2s;
    }

    .api-url-input input:focus {
      outline: none;
      border-color: var(--color-dark-gray);
    }

    .llm-key-input.required label {
      color: var(--color-error);
    }

    .llm-key-input.required input {
      border-color: var(--color-error);
    }

    .llm-key-banner {
      width: 100%;
      padding: 0.5rem 2rem 1rem;
      font-size: 0.8125rem;
      color: var(--color-error);
      background: var(--color-white);
    }

    .llm-key-banner a {
      color: inherit;
      text-decoration: underline;
    }

    .status-indicator {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.5rem 1rem;
      background: var(--color-background);
      border-radius: 20px;
      font-size: 0.875rem;
    }

    .status-indicator .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--color-error);
    }

    .status-indicator.online .dot {
      background: var(--color-success);
    }

    .status-indicator .text {
      color: var(--color-dark-gray);
      font-weight: 500;
    }

    .theme-toggle {
      padding: 0.5rem 1rem;
      border: 1px solid var(--color-input-border);
      border-radius: 20px;
      background: var(--color-background);
      color: var(--color-dark-gray);
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      transition: background-color 0.2s;
    }

    .theme-toggle:hover {
      background: var(--color-surface-hover-strong);
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
  themeService = inject(ThemeService);

  onApiUrlChange(url: string): void {
    this.apiService.setApiUrl(url);
  }

  onLlmApiKeyChange(key: string): void {
    this.apiService.setLlmApiKey(key);
  }
}
