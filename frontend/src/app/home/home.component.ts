import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subject, takeUntil } from 'rxjs';
import { WorkflowService } from '../services/workflow.service';
import { Workflow } from '../models/workflow.models';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="h-full overflow-y-auto home-bg">
      <div class="max-w-5xl mx-auto px-8 py-8">

        <!-- ── Hero ── -->
        <div class="home-hero mb-10">
          <div class="flex items-start justify-between gap-6">
            <div>
              <div class="home-hero-label">UPIAA Workflow Engine</div>
              <h1 class="home-hero-title">Your Workflows</h1>
              <p class="home-hero-sub">Build, connect, and run agentic AI automations visually</p>
            </div>
            <button class="home-new-btn" (click)="createWorkflow()">
              <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z"/></svg>
              New Workflow
            </button>
          </div>

          <!-- Stats row -->
          <div class="home-stats-row" *ngIf="workflows.length > 0">
            <div class="home-stat">
              <span class="home-stat-value">{{ workflows.length }}</span>
              <span class="home-stat-label">Workflows</span>
            </div>
            <div class="home-stat">
              <span class="home-stat-value">{{ totalNodes }}</span>
              <span class="home-stat-label">Nodes</span>
            </div>
            <div class="home-stat">
              <span class="home-stat-value home-stat-active">{{ activeCount }}</span>
              <span class="home-stat-label">Active</span>
            </div>
          </div>
        </div>

        <!-- ── Search bar ── -->
        <div class="home-search-wrap" *ngIf="workflows.length > 0">
          <svg class="home-search-icon" width="14" height="14" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/>
          </svg>
          <input class="home-search-input" type="text" placeholder="Search workflows…"
                 [(ngModel)]="searchQuery" (ngModelChange)="filterWorkflows()">
          <span *ngIf="searchQuery" class="home-search-clear" (click)="searchQuery=''; filterWorkflows()">✕</span>
        </div>

        <!-- ── Workflow Grid ── -->
        <div *ngIf="filteredWorkflows.length > 0" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          <div *ngFor="let wf of filteredWorkflows" class="wf-card group" (click)="openWorkflow(wf.id)">

            <!-- Card shimmer accent -->
            <div class="wf-card-shimmer"></div>

            <!-- Header row -->
            <div class="flex items-center justify-between mb-3">
              <div class="flex items-center gap-2.5">
                <div class="wf-card-icon">⚡</div>
                <span *ngIf="wf.is_published" class="active-badge">
                  <span class="active-dot"></span> Active
                </span>
              </div>
              <button class="wf-delete-btn opacity-0 group-hover:opacity-100"
                      (click)="deleteWorkflow(wf.id, $event)">🗑️</button>
            </div>

            <!-- Title -->
            <h3 class="text-sm font-semibold text-surface-100 mb-2 truncate">{{ wf.name }}</h3>

            <!-- Mini graph preview: node circles -->
            <div class="wf-mini-graph" *ngIf="wf.nodes.length > 0">
              <div class="wf-mini-node" *ngFor="let n of getMiniNodes(wf)" [title]="n.label"
                   [style.background]="n.color"></div>
              <span *ngIf="wf.nodes.length > 8" class="wf-mini-more">+{{ wf.nodes.length - 8 }}</span>
            </div>

            <!-- Meta chips -->
            <div class="flex items-center gap-2 mt-2.5">
              <span class="wf-chip wf-chip-nodes">{{ wf.nodes.length }} nodes</span>
              <span class="wf-chip wf-chip-edges">{{ wf.edges.length }} edges</span>
            </div>

            <!-- Relative timestamp -->
            <div class="text-[10px] text-surface-600 mt-3 font-mono" *ngIf="wf.updated_at">
              {{ relativeTime(wf.updated_at) }}
            </div>
          </div>
        </div>

        <!-- ── Empty search result ── -->
        <div *ngIf="filteredWorkflows.length === 0 && workflows.length > 0 && !loading"
             class="flex flex-col items-center justify-center py-16 text-surface-500">
          <span class="text-3xl mb-3 opacity-40">🔍</span>
          <p class="text-sm">No workflows match "{{ searchQuery }}"</p>
        </div>

        <!-- ── Empty State ── -->
        <div *ngIf="workflows.length === 0 && !loading"
             class="flex flex-col items-center justify-center py-24 text-surface-500">
          <div class="home-empty-icon">🧠</div>
          <p class="text-lg font-semibold mb-2 text-surface-300">No workflows yet</p>
          <p class="text-sm mb-6">Create your first agentic AI workflow to get started</p>
          <button class="home-new-btn" (click)="createWorkflow()">
            <svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z"/></svg>
            Create Workflow
          </button>
        </div>

        <!-- ── Loading ── -->
        <div *ngIf="loading" class="flex items-center justify-center py-24">
          <div class="home-loading-ring"></div>
          <span class="text-surface-500 text-sm ml-3">Loading workflows…</span>
        </div>

      </div>
    </div>
  `,
})
export class HomeComponent implements OnInit, OnDestroy {
  workflows: Workflow[] = [];
  filteredWorkflows: Workflow[] = [];
  loading = true;
  searchQuery = '';
  private destroy$ = new Subject<void>();

  get totalNodes(): number {
    return this.workflows.reduce((s, w) => s + (w.nodes?.length ?? 0), 0);
  }
  get activeCount(): number {
    return this.workflows.filter(w => w.is_published).length;
  }

  private readonly NODE_COLORS: Record<string, string> = {
    model_action: '#f59e0b', ollama_action: '#8b5cf6', openai_action: '#8b5cf6',
    gemini_action: '#6366f1', openrouter_action: '#7c3aed', if_else: '#3b82f6',
    for_loop: '#6366f1', while_loop: '#6366f1', http_request: '#2dd4bf',
    send_email: '#10b981', receive_email: '#14b8a6', timer: '#f59e0b',
    timer_cron: '#f59e0b', web_scrape: '#8b5cf6', web_search_scrape: '#3b82f6',
    webhook_trigger: '#8b5cf6', telegram_trigger: '#3b82f6',
    discord_receive_messages: '#5865F2', discord_notify: '#5865F2',
    discord_send_attachment: '#5865F2', telegram_send_document: '#0088cc',
  };

  getMiniNodes(wf: Workflow): { label: string; color: string }[] {
    return (wf.nodes || []).slice(0, 8).map(n => ({
      label: n.label || n.type,
      color: this.NODE_COLORS[n.type] || '#6366f1',
    }));
  }

  relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 1)  return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 7)  return `${d}d ago`;
    return new Date(iso).toLocaleDateString();
  }

  filterWorkflows(): void {
    const q = this.searchQuery.toLowerCase().trim();
    this.filteredWorkflows = q
      ? this.workflows.filter(w => w.name.toLowerCase().includes(q))
      : [...this.workflows];
  }

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
      next: (wfs) => { this.workflows = wfs; this.filteredWorkflows = [...wfs]; this.loading = false; },
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
