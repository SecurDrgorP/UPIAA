import { Component, EventEmitter, Input, Output, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { NodeTypeInfo, NodeCategory } from '../models/workflow.models';
import { WorkflowService } from '../services/workflow.service';

interface CategoryGroup {
  name: NodeCategory;
  icon: string;
  nodes: NodeTypeInfo[];
}

@Component({
  selector: 'app-node-palette',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="node-palette-panel" (click)="$event.stopPropagation()">
      <!-- Header -->
      <div class="flex items-center justify-between px-4 py-3 border-b border-surface-800">
        <h3 class="text-sm font-semibold text-surface-200">Add Node</h3>
        <button class="text-surface-500 hover:text-surface-200 text-xs" (click)="close.emit()">✕</button>
      </div>

      <!-- Search & AI Generator -->
      <div class="px-4 py-2 border-b border-surface-800">
        <input class="form-input text-xs w-full mb-2"
            type="text"
            placeholder="Search nodes..."
            [(ngModel)]="searchQuery"
            (ngModelChange)="filterNodes()" />
            
        <div class="flex flex-col gap-2 mt-2 pt-2 border-t border-surface-800">
          <div class="text-[10px] font-bold text-surface-500 uppercase flex items-center gap-1">✨ Generate Custom Node</div>
          <textarea class="form-input text-xs w-full resize-none min-h-[60px]"
              placeholder="e.g. A node that converts PDF to text..."
              [(ngModel)]="aiNodePrompt"></textarea>
          <button class="btn-primary py-1.5 px-3 text-xs flex justify-center items-center gap-2"
              [disabled]="isGeneratingNode || !aiNodePrompt.trim()"
              (click)="generateNode()">
            <span *ngIf="isGeneratingNode" class="animate-spin inline-block">↻</span>
            <span>{{ isGeneratingNode ? 'Generating...' : 'Generate with AI' }}</span>
          </button>
          <div *ngIf="nodeGenError" class="text-red-400 text-[10px]">{{ nodeGenError }}</div>
        </div>
      </div>

      <!-- Categories -->
      <div class="overflow-y-auto flex-1 px-2 pb-4">
        <div *ngFor="let group of filteredGroups" class="mb-3">
          <div class="px-2 py-1.5 text-[10px] font-bold text-surface-500 uppercase tracking-widest">
            {{ group.icon }} {{ group.name }}
          </div>
          <div *ngFor="let nt of group.nodes"
              class="palette-node-item"
              (click)="addNode.emit(nt)">
            <span class="text-base flex-shrink-0">{{ nt.icon }}</span>
            <div class="min-w-0">
              <div class="text-xs font-medium text-surface-200 truncate">{{ nt.label }}</div>
              <div class="text-[10px] text-surface-500 truncate">{{ nt.description }}</div>
            </div>
          </div>
        </div>

        <!-- No results -->
        <div *ngIf="filteredGroups.length === 0"
            class="text-center py-8 text-surface-500 text-xs">
          No nodes match "{{ searchQuery }}"
        </div>
      </div>
    </div>
  `,
})
export class NodePaletteComponent implements OnInit {
  @Output() addNode = new EventEmitter<NodeTypeInfo>();
  @Output() close = new EventEmitter<void>();

  searchQuery = '';
  allNodeTypes: NodeTypeInfo[] = [];
  categoryGroups: CategoryGroup[] = [];
  filteredGroups: CategoryGroup[] = [];

  aiNodePrompt = '';
  isGeneratingNode = false;
  nodeGenError: string | null = null;

  private categoryIcons: Record<string, string> = {
    'Model Actions': '🧠',
    'Agent Actions': '🤖',
    'Logic': '🔀',
    'Actions': '🌐',
    'Control Flow': '⏱️',
    'Triggers': '🔗',
    'Communication': '📡',
  };

  constructor(private workflowService: WorkflowService) { }

  ngOnInit(): void {
    this.workflowService.getNodeTypes().subscribe({
      next: (types: NodeTypeInfo[]) => {
        this.allNodeTypes = types;
        this.buildGroups();
      },
      error: () => {
        // Use fallback defaults
        this.allNodeTypes = this.getDefaults();
        this.buildGroups();
      },
    });
  }

  filterNodes(): void {
    const q = this.searchQuery.toLowerCase().trim();
    if (!q) {
      this.filteredGroups = [...this.categoryGroups];
      return;
    }
    this.filteredGroups = this.categoryGroups
      .map(g => ({
        ...g,
        nodes: g.nodes.filter(n =>
          n.label.toLowerCase().includes(q) ||
          n.description.toLowerCase().includes(q) ||
          n.type.toLowerCase().includes(q)
        ),
      }))
      .filter(g => g.nodes.length > 0);
  }

  generateNode(): void {
    if (!this.aiNodePrompt.trim() || this.isGeneratingNode) return;
    
    this.isGeneratingNode = true;
    this.nodeGenError = null;

    fetch(`/api/ai/build-node`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: this.aiNodePrompt })
    }).then(async (res) => {
        this.isGeneratingNode = false;
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            this.nodeGenError = err.error || 'Failed to generate node';
            return;
        }
        const data = await res.json();
        if (data && data.type) {
            this.aiNodePrompt = '';
            this.allNodeTypes.unshift(data);
            this.buildGroups();
            this.addNode.emit(data);
        } else {
            this.nodeGenError = 'Invalid response from server';
        }
    }).catch(err => {
        this.isGeneratingNode = false;
        this.nodeGenError = 'Network error while generating node';
    });
  }

  private buildGroups(): void {
    const map = new Map<NodeCategory, NodeTypeInfo[]>();
    for (const nt of this.allNodeTypes) {
      const cat = nt.category;
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(nt);
    }
    this.categoryGroups = Array.from(map.entries()).map(([name, nodes]) => ({
      name,
      icon: this.categoryIcons[name] || '📦',
      nodes,
    }));
    this.filteredGroups = [...this.categoryGroups];
  }

  private getDefaults(): NodeTypeInfo[] {
    return [
      { type: 'model_action', label: 'ML Model', icon: '🧠', description: 'Run prediction using a trained ML/DL model', category: 'Model Actions', default_config: { model_name: '', algorithm: 'RandomForest', task_type: 'classification', features: [], model_path: '', input_type: 'features', preprocessing: '{}' } },
      { type: 'ollama_action', label: 'Ollama Action', icon: '🦙', description: 'Execute a task using an Ollama LLM', category: 'LLM Actions', default_config: { ollama_url: 'http://localhost:11434', model: 'llama3.1', prompt_template: '', system_prompt: '', temperature: 0.7 } },
      { type: 'openai_action', label: 'OpenAI Action', icon: '🧠', description: 'Execute a task using OpenAI (ChatGPT)', category: 'LLM Actions', default_config: { api_key: '', model: 'gpt-4o', prompt_template: '', system_prompt: '', temperature: 0.7 } },
      { type: 'gemini_action', label: 'Gemini Action', icon: '✨', description: 'Execute a task using Google Gemini', category: 'LLM Actions', default_config: { api_key: '', model: 'gemini-1.5-pro', prompt_template: '', system_prompt: '', temperature: 0.7 } },
      { type: 'openrouter_action', label: 'OpenRouter Action', icon: '🌐', description: 'Execute a task using OpenRouter APIs', category: 'LLM Actions', default_config: { api_key: '', model: 'meta-llama/llama-3-70b-instruct', prompt_template: '', system_prompt: '', temperature: 0.7 } },
      { type: 'if_else', label: 'If / Else', icon: '🔀', description: 'Conditional branching', category: 'Logic', default_config: { condition: '', true_branch: '', false_branch: '' } },
      { type: 'for_loop', label: 'For Loop', icon: '🔁', description: 'Iterate over a list', category: 'Logic', default_config: { iterable_key: '', item_variable: 'item', max_iterations: 100 } },
      { type: 'while_loop', label: 'While Loop', icon: '🔄', description: 'Repeat while condition is true', category: 'Logic', default_config: { condition: '', max_iterations: 100 } },
      { type: 'http_request', label: 'HTTP Request', icon: '🌐', description: 'Make an HTTP request', category: 'Actions', default_config: { url: '', method: 'GET', headers: {}, body: {} } },
      { type: 'send_email', label: 'Send Email', icon: '📤', description: 'Send email via Gmail SMTP', category: 'Communication', default_config: { smtp_host: 'smtp.gmail.com', smtp_port: 587, email: '', app_password: '', to: '', subject: '', body: '', content_type: 'plain' } },
      { type: 'receive_email', label: 'Email Listener (IMAP)', icon: '📬', description: 'Blocking listener that waits for a specific email to arrive via IMAP', category: 'Communication', default_config: { imap_host: 'imap.gmail.com', imap_port: 993, email: '', app_password: '', folder: 'INBOX', subject_filter_mode: 'none', subject_filter_value: '', subject_case_sensitive: false, output_mapping: '' } },
      { type: 'timer', label: 'Timer', icon: '⏰', description: 'Schedule workflow execution', category: 'Triggers', default_config: { schedule_type: 'interval', interval_seconds: 3600, cron_expression: '0 0 * * *' } },
      { type: 'timer_cron', label: 'Cron Timer', icon: '⏰', description: 'Schedule tasks with complex recurrence (daily, weekly, etc.)', category: 'Control Flow', default_config: { trigger_time: '', recurrence: 'daily', week_day: null, month_day: null, year_day: null, year_month: null, timezone: 'UTC', output_mapping: '' } },
      { type: 'web_scrape', label: 'Web Scrape', icon: '🕷️', description: 'Deep crawl and scrape semantic content from websites', category: 'Actions', default_config: { url: '', extra_urls: '', crawl_depth: null, same_domain_only: true, max_pages: null, concurrency: null, output_format: 'full', output_mapping: '' } },
      { type: 'web_search_scrape', label: 'Search & Scrape', icon: '🔍', description: 'Search the web and scrape top results automatically', category: 'Actions', default_config: { query: '', search_provider: 'brave', api_key: '', google_cx: '', result_count: null, scrape_results: true, scrape_concurrency: null, output_mapping: '' } },
      { type: 'webhook_trigger', label: 'Webhook Trigger', icon: '🔗', description: 'Listen for incoming HTTP requests to trigger a workflow', category: 'Triggers', default_config: { path: '/my-webhook', methods: ['POST'], secret_token: '', response_mode: 'immediate', timeout_hours: 24 } },
      { type: 'telegram_trigger', label: 'Telegram Trigger', icon: '💬', description: 'Listen for incoming Telegram messages', category: 'Triggers', default_config: { bot_token: '', allowed_chat_ids: '', allowed_usernames: '', filter_message_types: 'text', poll_timeout: 25 } },
      { type: 'discord_send_attachment', label: 'Discord: Send File', icon: '📎', description: 'Send a file attachment to a Discord channel', category: 'Communication', default_config: { bot_token: '', channel_id: '', file_path: '', message: '', embed_enabled: false } },
      { type: 'telegram_send_document', label: 'Telegram: Send Document', icon: '📎', description: 'Send a file as a document to a Telegram chat', category: 'Communication', default_config: { bot_token: '', chat_id: '', file_path: '', caption: '', parse_mode: 'Markdown', disable_notification: false } },
    ];
  }
}
