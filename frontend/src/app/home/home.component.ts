import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { Subject, takeUntil } from 'rxjs';
import { WorkflowService } from '../services/workflow.service';
import { Workflow } from '../models/workflow.models';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="h-full overflow-y-auto px-8 py-6" style="background: linear-gradient(180deg, #020617 0%, #0a0f1e 50%, #020617 100%);">
      <!-- Hero -->
      <div class="max-w-5xl mx-auto">
        <div class="flex items-center justify-between mb-10">
          <div>
            <h2 class="text-3xl font-bold mb-1.5" style="background: linear-gradient(135deg, #e2e8f0 0%, #94a3b8 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Your Workflows</h2>
            <p class="text-sm text-surface-500">
              Build, manage, and execute agentic AI workflows
            </p>
          </div>
          <button class="toolbar-btn primary text-sm px-5 py-2.5" (click)="createWorkflow()" style="border-radius: 12px;">
            <span>＋</span> New Workflow
          </button>
        </div>

        <!-- Workflow Grid -->
        <div *ngIf="workflows.length > 0"
            class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          <div *ngFor="let wf of workflows"
              class="wf-card group"
              (click)="openWorkflow(wf.id)">
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2">
                <div style="width: 36px; height: 36px; border-radius: 10px; background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(139,92,246,0.15)); display: flex; align-items: center; justify-content: center;">
                  <span class="text-lg">⚡</span>
                </div>
                <span *ngIf="wf.is_published" class="active-badge">
                  <span class="active-dot"></span> Active
                </span>
              </div>
              <button class="opacity-0 group-hover:opacity-100 text-surface-500 hover:text-danger text-xs transition-opacity"
                  style="padding: 4px 8px; border-radius: 6px; background: rgba(239,68,68,0.08);"
                  (click)="deleteWorkflow(wf.id, $event)">🗑️ Delete</button>
            </div>
            <h3 class="text-sm font-semibold text-surface-100 mb-2 truncate">{{ wf.name }}</h3>
            <div class="flex items-center gap-2 text-xs text-surface-500">
              <span style="background: rgba(99,102,241,0.1); color: #818cf8; padding: 2px 8px; border-radius: 6px; font-weight: 500;">{{ wf.nodes.length || 0 }} nodes</span>
              <span style="background: rgba(51,65,85,0.4); padding: 2px 8px; border-radius: 6px;">{{ wf.edges.length || 0 }} edges</span>
            </div>
            <div class="text-[10px] text-surface-600 mt-3 font-mono" *ngIf="wf.created_at">
              {{ wf.created_at | date:'medium' }}
            </div>
          </div>
        </div>

        <!-- Empty State -->
        <div *ngIf="workflows.length === 0 && !loading"
            class="flex flex-col items-center justify-center py-24 text-surface-500">
          <div style="width: 80px; height: 80px; border-radius: 20px; background: linear-gradient(135deg, rgba(99,102,241,0.1), rgba(139,92,246,0.1)); display: flex; align-items: center; justify-content: center; margin-bottom: 20px;">
            <span class="text-4xl">🧠</span>
          </div>
          <p class="text-lg font-medium mb-2 text-surface-300">No workflows yet</p>
          <p class="text-sm mb-6">Create your first agentic AI workflow to get started</p>
          <button class="toolbar-btn primary" (click)="createWorkflow()" style="border-radius: 12px;">
            <span>＋</span> Create Workflow
          </button>
        </div>

        <!-- Loading -->
        <div *ngIf="loading" class="flex items-center justify-center py-24">
          <span class="text-surface-500 text-sm animate-pulse">Loading workflows...</span>
        </div>
      </div>
    </div>
  `,
})
export class HomeComponent implements OnInit, OnDestroy {
  workflows: Workflow[] = [];
  loading = true;
  private destroy$ = new Subject<void>();

  constructor(
    private router: Router,
    private workflowService: WorkflowService,
  ) { }

  ngOnInit(): void {
    this.loadWorkflows();
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  loadWorkflows(): void {
    this.loading = true;
    this.workflowService.listWorkflows().pipe(
      takeUntil(this.destroy$)
    ).subscribe({
      next: (wfs) => { this.workflows = wfs; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  createWorkflow(): void {
    this.router.navigate(['/editor']);
  }

  openWorkflow(id: string): void {
    this.router.navigate(['/editor', id]);
  }

  deleteWorkflow(id: string, event: MouseEvent): void {
    event.stopPropagation();
    this.workflowService.deleteWorkflow(id).pipe(
      takeUntil(this.destroy$)
    ).subscribe({
      next: () => {
        this.workflows = this.workflows.filter(w => w.id !== id);
      },
    });
  }
}
