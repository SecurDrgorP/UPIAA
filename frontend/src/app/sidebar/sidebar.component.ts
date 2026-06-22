import { Component, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule, JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { GraphNode, GraphLink, NodeType, NodeTypeInfo, ConfigFieldDescriptor } from '../models/workflow.models';
import { WorkflowService } from '../services/workflow.service';

@Component({
    selector: 'app-sidebar',
    standalone: true,
    imports: [CommonModule, FormsModule, JsonPipe],
    templateUrl: './sidebar.component.html',
})
export class SidebarComponent implements OnChanges {
    @Input() node!: GraphNode;
    @Input() allNodes: GraphNode[] = [];
    @Input() allEdges: GraphLink[] = [];
    @Input() isLocked: boolean = false;
    @Output() configUpdate = new EventEmitter<Record<string, any>>();
    @Output() labelUpdate = new EventEmitter<string>();
    @Output() configChanged = new EventEmitter<{ key: string; value: any }>();
    @Output() close = new EventEmitter<void>();
    @Output() deleteNode = new EventEmitter<void>();

    constructor(
        private http: HttpClient,
        private workflowService: WorkflowService
    ) { }

    allNodeTypes: NodeTypeInfo[] = [];

    ngOnInit(): void {
        this.workflowService.getNodeTypes().subscribe(types => {
            this.allNodeTypes = types;
            this.computeUpstreamData();

            // Rebuild fields now that we have the real schemas —
            // ngOnChanges fires before this, so the initial build uses
            // introspection fallback (no dropdowns). This second build
            // picks up the real schema with select/options/visibleWhen.
            if (this.node?.data) {
                this.cachedFields = this.buildConfigFields();
                this.initializeSchemaDefaults();
            }
        });
    }

    editLabel = '';
    configJson = '';
    jsonError = '';
    activeTab: 'config' | 'input' | 'output' = 'config';

    /** Upstream nodes connected to this node */
    upstreamNodes: { id: string; label: string; result: any }[] = [];
    /** Merged input data preview */
    inputData: Record<string, any> = {};
    /** Copyable expression hints */
    expressionHints: { expr: string; label: string; preview?: string }[] = [];

    /** Password/secret field visibility toggles */
    secretVisibility: Record<string, boolean> = {};

    /** Which field's expression picker is currently open */
    activeExpressionPicker: string | null = null;

    /** Whether the raw JSON editor is expanded */
    showRawJson = false;
    /** Cached config fields for the current node */
    cachedFields: ConfigFieldDescriptor[] = [];

    /** Docs panel state */
    showDocs = false;
    nodeDoc = '';

    // ── UI State ──────────────────────────────────────────────────
    uploading = false;

    ngOnChanges(changes: SimpleChanges): void {
        if (changes['node'] && this.node) {
            // Only reset heavy state if the node ID actually changed
            const isNewNode = changes['node'].previousValue?.id !== this.node.id;

            this.editLabel = this.node.label;
            
            // Only update raw JSON if we aren't currently typing in it, avoiding cursor jump
            if (isNewNode) {
                this.configJson = JSON.stringify(this.node.data.config, null, 2);
                this.jsonError = '';
                this.secretVisibility = {};
                this.activeTab = this.node.data.result ? 'output' : 'config';
                this.showDocs = false;
                this.nodeDoc = '';
            }

            this.computeUpstreamData();

            // Build config fields from schema and initialize defaults
            this.cachedFields = this.buildConfigFields();
            this.initializeSchemaDefaults();
        }
        if (changes['allNodes'] || changes['allEdges']) {
            this.computeUpstreamData();
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // UNIVERSAL CONFIG AUTO-DESIGNER
    // ═══════════════════════════════════════════════════════════════

    /**
     * buildConfigFields() — The universal auto-designer.
     *
     * Reads config_schema from the backend catalog (either list or dict format)
     * and produces an ordered array of ConfigFieldDescriptor objects that the
     * template auto-renders into fully-styled form controls.
     *
     * This single function replaces ~860 lines of per-node-type hardcoded HTML.
     */
    buildConfigFields(): ConfigFieldDescriptor[] {
        if (!this.node?.data) return [];

        const typeInfo = this.allNodeTypes.find(t => t.type === this.node.data.type);
        const schema = typeInfo?.config_schema;
        const config = this.node.data.config || {};

        let rawFields: any[];

        if (Array.isArray(schema)) {
            // List-based schema (webhook_trigger, telegram_trigger, etc.)
            rawFields = schema;
        } else if (schema && typeof schema === 'object') {
            // Dict-based schema (ollama, http_request, etc.) — convert to list preserving key order
            rawFields = Object.entries(schema).map(([key, def]: [string, any]) => ({
                key,
                ...(typeof def === 'object' ? def : { type: typeof def }),
            }));
        } else {
            // No schema at all — introspect from the config object keys
            rawFields = Object.keys(config).map(key => {
                const val = config[key];
                return {
                    key,
                    type: typeof val === 'boolean' ? 'boolean'
                        : typeof val === 'number' ? 'number'
                        : (val !== null && typeof val === 'object') ? 'json'
                        : 'string',
                };
            });
        }

        return rawFields.map((f: any) => {
            // Normalize options: can be [{value, label}] or ["string", ...]
            let options = f.options || null;
            if (f.enum && !options) {
                options = f.enum.map((v: string) => ({ value: v, label: v }));
            }
            if (Array.isArray(options)) {
                options = options.map((o: any) =>
                    typeof o === 'string' ? { value: o, label: o } : o
                );
            }

            // Auto-promote field type based on metadata
            let fieldType = f.type || 'string';
            if (options && fieldType === 'string') {
                fieldType = 'select';  // string + options/enum → dropdown
            }

            return {
                key: f.key,
                type: fieldType,
                label: f.label || f.key.replace(/_/g, ' ').replace(/\b\w/g, (l: string) => l.toUpperCase()),
                description: f.description || '',
                placeholder: f.placeholder || '',
                required: f.required || false,
                secret: f.secret || false,
                default: f.default,
                options,
                multiline: f.multiline || false,
                visibleWhen: f.visibleWhen || null,
                template: f.template || false,
                dotpath: f.dotpath || false,
                min: f.min,
                max: f.max,
            } as ConfigFieldDescriptor;
        });
    }

    /**
     * Initialize missing config values from schema defaults.
     * Replaces the old hardcoded ensureNodeDefaults() switch-case.
     */
    initializeSchemaDefaults(): void {
        if (!this.node?.data?.config) return;
        const cfg = this.node.data.config;

        for (const field of this.cachedFields) {
            if (!(field.key in cfg) && field.default !== undefined) {
                cfg[field.key] = field.default;
            }
        }
    }

    /**
     * Check if a field should be visible based on visibleWhen conditions.
     * Supports both single values and arrays:
     *   { "recurrence": "weekly" }         → show when config.recurrence === "weekly"
     *   { "method": ["POST", "PUT"] }      → show when config.method is "POST" or "PUT"
     */
    isFieldVisible(field: ConfigFieldDescriptor): boolean {
        if (!field.visibleWhen) return true;

        const cfg = this.node?.data?.config || {};
        for (const [key, expected] of Object.entries(field.visibleWhen)) {
            const actual = cfg[key];
            if (Array.isArray(expected)) {
                // Array: field is visible if actual value is one of the expected values
                if (!expected.includes(actual)) return false;
            } else {
                // Single value: strict equality
                if (actual !== expected) return false;
            }
        }
        return true;
    }

    /** trackBy for Angular *ngFor to prevent focus loss during typing */
    trackByField(index: number, field: ConfigFieldDescriptor): string {
        return field.key;
    }

    /** Toggle password visibility for a secret field */
    toggleSecretVisibility(key: string): void {
        this.secretVisibility[key] = !this.secretVisibility[key];
    }

    /** Toggle the expression picker for a specific field */
    toggleExpressionPicker(fieldKey: string): void {
        this.activeExpressionPicker = this.activeExpressionPicker === fieldKey ? null : fieldKey;
    }

    /**
     * Insert an expression into a config field.
     * If the field already has content, appends the expression at the end
     * rather than replacing it — allows building compound prompts.
     */
    insertExpression(fieldKey: string, expr: string): void {
        if (!this.node?.data?.config) return;
        const current = this.node.data.config[fieldKey] || '';
        this.node.data.config[fieldKey] = current ? `${current} ${expr}` : expr;
        this.activeExpressionPicker = null;
        this.emitConfigChange();
    }

    // ═══════════════════════════════════════════════════════════════
    // CONFIG CHANGE HANDLERS
    // ═══════════════════════════════════════════════════════════════

    /** Emit config change from form fields (auto-save) */
    emitConfigChange(): void {
        this.configUpdate.emit({ ...this.node.data.config });
        this.configJson = JSON.stringify(this.node.data.config, null, 2);
    }

    onConfigChange(): void {
        this.emitConfigChange();
    }

    /**
     * Called when a select dropdown changes.
     * Clears the values of fields that become hidden due to `visibleWhen`
     * so the previous selection's params don't merge with the new one.
     */
    onSelectChange(key: string, value: any): void {
        // 1. Update local config immediately for instant visual feedback
        if (this.node?.data?.config) {
            this.node.data.config[key] = value;
        }
        // 2. Emit to canvas so the authoritative node object is updated
        this.configChanged.emit({ key, value });

        // 3. Reset visibleWhen-dependent fields that are now hidden
        for (const field of this.cachedFields) {
            if (field.key === key) continue;
            if (field.visibleWhen && !this.isFieldVisible(field)) {
                const resetVal = field.default !== undefined ? field.default : null;
                if (resetVal !== null) {
                    this.node.data.config[field.key] = resetVal;
                    this.configChanged.emit({ key: field.key, value: resetVal });
                } else {
                    delete this.node.data.config[field.key];
                    this.configChanged.emit({ key: field.key, value: undefined });
                }
            }
        }
        this.emitConfigChange();
    }

    /** Handle dynamic field changes with type coercion */
    onFieldChange(key: string, value: any, type: string): void {
        if (!this.node?.data) return;

        if (type === 'number') {
            value = this.sanitizeNumber(value);
        } else if (type === 'boolean') {
            value = !!value;
        } else if (type === 'json' || type === 'object') {
            try {
                value = JSON.parse(value);
                this.jsonError = '';
            } catch {
                return; // ignore while typing
            }
        }

        this.node.data.config[key] = value;
        this.emitConfigChange();
    }

    /** Handle multiselect toggle */
    onMultiselectToggle(key: string, optionValue: string, checked: boolean): void {
        if (!this.node?.data?.config) return;
        let current: string[] = this.node.data.config[key] || [];
        if (!Array.isArray(current)) current = [];

        if (checked) {
            if (!current.includes(optionValue)) current = [...current, optionValue];
        } else {
            current = current.filter((v: string) => v !== optionValue);
        }
        this.node.data.config[key] = current;
        this.emitConfigChange();
    }

    /** Check if multisect option is selected */
    isMultiselectChecked(key: string, optionValue: string): boolean {
        const val = this.node?.data?.config?.[key];
        return Array.isArray(val) && val.includes(optionValue);
    }

    sanitizeNumber(v: any): number | null {
        const n = Number(v);
        return (v === null || v === '' || isNaN(n)) ? null : n;
    }

    // ═══════════════════════════════════════════════════════════════
    // UPSTREAM DATA & EXPRESSION HINTS
    // ═══════════════════════════════════════════════════════════════

    computeUpstreamData(): void {
        if (!this.node || !this.allEdges) return;

        const upstreamIds = this.allEdges
            .filter(e => e.target === this.node.id)
            .map(e => e.source);

        this.upstreamNodes = (this.allNodes || [])
            .filter(n => upstreamIds.includes(n.id))
            .map(n => ({ id: n.id, label: n.label || n.id, result: n.data?.result }));

        this.inputData = {};
        for (const up of this.upstreamNodes) {
            if (up.result && typeof up.result === 'object') {
                Object.assign(this.inputData, up.result);
            }
        }

        this.expressionHints = [];
        for (const up of this.upstreamNodes) {
            let result = up.result;
            const isLive = !!result;
            if (!result || Object.keys(result).length === 0) {
                const nodeRef = this.allNodes.find(n => n.id === up.id);
                if (nodeRef) {
                    const typeInfo = this.allNodeTypes.find(t => t.type === nodeRef.data.type);
                    if (typeInfo?.example_output) result = typeInfo.example_output;
                }
            }
            if (!result || typeof result !== 'object') continue;

            for (const [key, val] of Object.entries(result)) {
                if (key === '__meta__') continue;

                // Top-level field
                this.expressionHints.push({
                    expr: `{{${up.id}.${key}}}`,
                    label: `${up.label} › ${key}`,
                    preview: isLive ? JSON.stringify(val) : `(example) ${JSON.stringify(val)}`,
                });

                // Expand arrays: show [0].subfield hints
                if (Array.isArray(val) && val.length > 0 && typeof val[0] === 'object' && val[0] !== null) {
                    for (const subKey of Object.keys(val[0])) {
                        this.expressionHints.push({
                            expr: `{{${up.id}.${key}[0].${subKey}}}`,
                            label: `${up.label} › ${key}[0].${subKey}`,
                            preview: isLive
                                ? JSON.stringify(val[0][subKey])
                                : `(example) ${JSON.stringify(val[0][subKey])}`,
                        });
                    }
                }

                // Expand nested objects (one level deep)
                if (!Array.isArray(val) && typeof val === 'object' && val !== null) {
                    for (const [subKey, subVal] of Object.entries(val)) {
                        this.expressionHints.push({
                            expr: `{{${up.id}.${key}.${subKey}}}`,
                            label: `${up.label} › ${key}.${subKey}`,
                            preview: isLive ? JSON.stringify(subVal) : `(example) ${JSON.stringify(subVal)}`,
                        });
                    }
                }
            }
        }
    }

    copyExpression(expr: string): void {
        navigator.clipboard.writeText(expr);
    }

    // ═══════════════════════════════════════════════════════════════
    // RAW JSON EDITOR
    // ═══════════════════════════════════════════════════════════════

    applyConfig(): void {
        try {
            const parsed = JSON.parse(this.configJson);
            this.configUpdate.emit(parsed);
            this.jsonError = '';
        } catch (e) {
            this.jsonError = 'Invalid JSON';
        }
    }

    /** Fetch node documentation from /api/node-docs and display it */
    fetchNodeDocs(): void {
        this.showDocs = !this.showDocs;
        if (!this.showDocs) return;
        if (this.nodeDoc) return; // already fetched
        this.http.get<Record<string, any>>('/api/node-docs').subscribe({
            next: (docs) => {
                const nodeType = this.node?.data?.type;
                if (nodeType && docs[nodeType]) {
                    this.nodeDoc = docs[nodeType].doc || 'No documentation available for this node type.';
                } else {
                    this.nodeDoc = 'No documentation available for this node type.';
                }
            },
            error: () => {
                this.nodeDoc = 'Failed to load documentation.';
            },
        });
    }

    applyLabel(): void {
        if (this.editLabel.trim()) {
            this.labelUpdate.emit(this.editLabel.trim());
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // HELPERS
    // ═══════════════════════════════════════════════════════════════

    /** Resolve which node is connected to a specific output handle */
    getBranchTarget(handle: string): string {
        const edge = this.allEdges.find(e => e.source === this.node.id && e.sourceHandle === handle);
        if (!edge) return 'Not connected';
        const target = this.allNodes.find(n => n.id === edge.target);
        return target ? target.label : edge.target;
    }

    uploadModel(event: any): void {
        const file = event.target.files?.[0];
        if (!file) return;

        this.uploading = true;
        this.workflowService.uploadModel(file).subscribe({
            next: (res) => {
                this.node.data.config['model_path'] = res.path;
                this.emitConfigChange();
                this.uploading = false;
            },
            error: (err) => {
                console.error('Upload failed:', err);
                this.uploading = false;
                alert('Model upload failed. Please check the backend connection.');
            }
        });
    }

    getNodeColor(type: NodeType): string {
        const map: Record<string, string> = {
            http_request: '#2dd4bf',
            model_action: '#f59e0b',
            ollama_action: '#8b5cf6',
            openai_action: '#8b5cf6',
            gemini_action: '#8b5cf6',
            openrouter_action: '#8b5cf6',
            if_else: '#3b82f6',
            for_loop: '#6366f1',
            while_loop: '#6366f1',
            send_email: '#10b981',
            receive_email: '#14b8a6',
            timer: '#f59e0b',
            timer_cron: '#f59e0b',
            web_scrape: '#8b5cf6',
            web_search_scrape: '#3b82f6',
            webhook_trigger: '#8b5cf6',
            telegram_trigger: '#3b82f6',
            discord_receive_messages: '#5865F2',
            discord_notify: '#5865F2',
            discord_send_attachment: '#5865F2',
            telegram_send_document: '#0088cc',
        };
        return map[type] || '#6366f1';
    }

    getNodeIcon(type: NodeType): string {
        const map: Record<string, string> = {
            http_request: '🌐',
            model_action: '🧠',
            ollama_action: '🦙',
            openai_action: '🧠',
            gemini_action: '✨',
            openrouter_action: '🌐',
            if_else: '🔀',
            for_loop: '🔁',
            while_loop: '🔄',
            send_email: '📤',
            receive_email: '📬',
            timer: '⏰',
            timer_cron: '⏰',
            web_scrape: '🕷️',
            web_search_scrape: '🔍',
            webhook_trigger: '🔗',
            telegram_trigger: '💬',
            discord_receive_messages: '📨',
            discord_notify: '💬',
            discord_send_attachment: '📎',
            telegram_send_document: '📎',
        };
        return map[type] || '📦';
    }

    getResultJson(): string {
        return this.node?.data?.result
            ? JSON.stringify(this.node.data.result, null, 2)
            : 'No execution result yet. Run the workflow to see actual output here.';
    }

    getExampleOutputJson(): string {
        if (!this.node?.data) return '';
        const typeInfo = this.allNodeTypes.find(t => t.type === this.node.data.type);
        return typeInfo?.example_output
            ? JSON.stringify(typeInfo.example_output, null, 2)
            : 'No example output provided for this node type.';
    }

    getInputJson(): string {
        return Object.keys(this.inputData).length > 0
            ? JSON.stringify(this.inputData, null, 2)
            : 'No upstream data yet. Connect a node above this one and run the workflow.';
    }
}
