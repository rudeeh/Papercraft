import { Component, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HeaderComponent } from './components/header/header.component';
import { UploadComponent } from './components/upload/upload.component';
import { StatusComponent } from './components/status/status.component';
import { ChatComponent } from './components/chat/chat.component';
import { RecentTasksComponent } from './components/recent-tasks/recent-tasks.component';
import { GraphQueryComponent } from './components/graph-query/graph-query.component';
import { PaperGraphComponent } from './components/paper-graph/paper-graph.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    HeaderComponent,
    UploadComponent,
    StatusComponent,
    ChatComponent,
    RecentTasksComponent,
    GraphQueryComponent,
    PaperGraphComponent
  ],
  template: `
    <app-header />

    <main class="main-content">
      <div class="container">
        <div class="grid">
          <div class="grid-col">
            <app-upload (taskUploaded)="onTaskUploaded($event)" />
            <app-status #statusComponent />
            <app-paper-graph #paperGraphComponent />
          </div>
          <div class="grid-col">
            <app-chat #chatComponent />
            <app-recent-tasks
              (checkStatus)="onCheckStatus($event)"
              (useInChat)="onUseInChat($event)"
            />
            <app-graph-query (viewPaperGraph)="onViewPaperGraph($event)" />
          </div>
        </div>
      </div>
    </main>

    <footer class="footer">
      <p>DocRAG - Document Retrieval-Augmented Generation</p>
    </footer>
  `,
  styles: [`
    :host {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }

    .main-content {
      flex: 1;
      background: #f5f5f5;
      padding: 2rem;
    }

    .container {
      max-width: 1200px;
      margin: 0 auto;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.5rem;
    }

    .grid-col {
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }

    .footer {
      background: #fff;
      border-top: 1px solid #e0e0e0;
      padding: 1rem 2rem;
      text-align: center;
    }

    .footer p {
      margin: 0;
      color: #666;
      font-size: 0.875rem;
    }

    @media (max-width: 900px) {
      .grid {
        grid-template-columns: 1fr;
      }

      .main-content {
        padding: 1rem;
      }
    }
  `]
})
export class AppComponent {
  @ViewChild('statusComponent') statusComponent!: StatusComponent;
  @ViewChild('chatComponent') chatComponent!: ChatComponent;
  @ViewChild('paperGraphComponent') paperGraphComponent!: PaperGraphComponent;

  onTaskUploaded(event: { taskId: string; docId?: string }): void {
    this.statusComponent.setTaskId(event.taskId);
    if (event.docId) {
      this.chatComponent.setDocId(event.docId);
    }
  }

  onCheckStatus(taskId: string): void {
    this.statusComponent.setTaskId(taskId);
  }

  onUseInChat(docId: string): void {
    this.chatComponent.setDocId(docId);
  }

  onViewPaperGraph(paperId: string): void {
    this.paperGraphComponent.setPaperId(paperId);
  }
}
