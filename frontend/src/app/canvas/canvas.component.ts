import { Component, OnInit, OnDestroy, HostListener, ElementRef, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { Subject, takeUntil } from 'rxjs';

import { SidebarComponent } from '../sidebar/sidebar.component';
import { NodePaletteComponent } from '../node-palette/node-palette.component';
import { WorkflowService } from '../services/workflow.service';
import { AiService, AiGenEvent } from '../services/ai.service';
import {
    GraphNode,
    GraphLink,
    WorkflowNode,
    WorkflowEdge,
    Workflow,
    NodeType,
    NodeTypeInfo,
    ExecutionStatus,
    ExecutionStatusType,
} from '../models/workflow.models';

/* ── Constants ──────────────────────────────────────────────── */
const NODE_W = 220;
const NODE_H = 80;

@Component({
    selector: 'app-canvas',
    standalone: true,
    imports: [CommonModule, FormsModule, SidebarComponent, NodePaletteComponent],
    templateUrl: './canvas.component.html',
})
export class CanvasComponent implements OnInit, OnDestroy {
    @ViewChild('canvasEl', { static: true }) canvasEl!: ElementRef<HTMLDivElement>;

    /* ── Data ────────────────────────────────────────────────── */
    nodes: GraphNode[] = [];
    links: GraphLink[] = [];
    workflowName = 'Untitled Workflow';
    workflowId: string | null = null;

    selectedNode: GraphNode | null = null;
    /** Node whose config sidebar is open (separate from selection) */
    sidebarNode: GraphNode | null = null;
    showPalette = false;
    isExecuting = false;
    isPublished = false;
    showExecDropdown = false;
    executionLog: ExecutionStatus[] = [];
    showLog = false;
    /** Workflow validation error shown to user before execution */
    validationError: string | null = null;

    /** Toast message shown after clipboard copy (shared by Copy JSON + Copy Results) */
    copyToast: string | null = null;
    private _copyToastTimer: ReturnType<typeof setTimeout> | null = null;

    /** Loaded node type catalog (used for secret stripping on export) */
    allNodeTypes: NodeTypeInfo[] = [];

    get isLocked(): boolean { return this.isExecuting || this.isPublished; }

    /* ── Canvas state ────────────────────────────────────────── */
    panX = 0;
    panY = 0;
    zoom = 1;

    /* ── AI Chat state ───────────────────────────────────────── */
    showAiChat = false;
    aiPrompt = '';
    isAiBuilding = false;
    aiBuildProgress = 0;
    aiWarningBanner: string | null = null;
    aiGenState: 'idle' | 'generating' | 'validating' | 'deploying' | 'done' | 'error' = 'idle';
    aiGenMessage = '';

    /* ── Drag states ─────────────────────────────────────────── */
    private draggingNode: GraphNode | null = null;
    private dragOffsetX = 0;
    private dragOffsetY = 0;

    private isPanning = false;
    private panStartX = 0;
    private panStartY = 0;

    /* ── Connection drag ─────────────────────────────────────── */
    isConnecting = false;
    connectSourceId: string | null = null;
    connectSourceLabel = '';
    tempLineX1 = 0;
    tempLineY1 = 0;
    tempLineX2 = 0;
    tempLineY2 = 0;

    /* ── Resize handle drag ──────────────────────────────────── */
    private resizingNode: GraphNode | null = null;
    private resizeStartX = 0;
    private resizeStartY = 0;
    private resizeStartW = 0;
    private resizeStartH = 0;

    /* ── Undo / Redo ─────────────────────────────────────────── */
    private undoStack: string[] = [];
    private redoStack: string[] = [];
    private readonly MAX_UNDO = 50;

    /* ── Internal ────────────────────────────────────────────── */
    private nodeCounter = 0;
    private destroy$ = new Subject<void>();
    private _saveTimer: ReturnType<typeof setTimeout> | null = null;

    /**
     * Tracks nodes that reached a terminal status (success/failed/error) during
     * the current run. Used to suppress trailing `skipped` events emitted by the
     * graph navigator after a loop body has already successfully completed —
     * otherwise the loop body would flash green then immediately go grey+hatched.
     */
    private resolvedThisRun = new Set<string>();

    readonly NODE_W = NODE_W;
    readonly NODE_H = NODE_H;

    constructor(
        private workflowService: WorkflowService,
        private http: HttpClient,
        private route: ActivatedRoute,
        private router: Router,
        private aiService: AiService,
    ) { }

    ngOnInit(): void {
        // Load node type catalog for secret-stripping
        this.workflowService.getNodeTypes().subscribe(types => {
            this.allNodeTypes = types;
        });

        // Load workflow from route param, or reset if creating a new one
        this.route.params.pipe(takeUntil(this.destroy$)).subscribe((params: Record<string, string>) => {
            if (params['id']) {
                this.workflowId = params['id'];
                this.loadWorkflow(params['id']);
            } else {
                // User navigated to /editor (New Workflow) - reset the state
                this.resetCanvasState();
            }
        });
    }

    ngOnDestroy(): void {
        this.destroy$.next();
        this.destroy$.complete();
        this.workflowService.disconnectWs();
        this.aiService.closeStream();
    }

    /* ═══════════════════════════════════════════════════════════
       Workflow Load / Save
       ═══════════════════════════════════════════════════════════ */

    private loadWorkflow(id: string): void {
        this.workflowService.getWorkflow(id).pipe(
            takeUntil(this.destroy$)
        ).subscribe({
            next: (wf: Workflow) => {
                this.workflowName = wf.name;
                this.workflowId = wf.id;
                this.nodes = wf.nodes.map((n: WorkflowNode) => ({
                    id: n.id,
                    label: n.label,
                    data: { type: n.type, config: n.config, status: 'idle' as ExecutionStatusType },
                    position: n.position,
                }));
                this.links = wf.edges.map((e: WorkflowEdge) => ({
                    id: e.id,
                    source: e.source,
                    target: e.target,
                    sourceHandle: e.sourceHandle
                }));

                // Robust nodeCounter: find max numeric suffix
                const suffixes = this.nodes
                    .map(n => parseInt(n.id.split('_')[1]))
                    .filter(n => !isNaN(n));
                this.nodeCounter = suffixes.length > 0 ? Math.max(...suffixes) : 0;

                // Check if this workflow is currently published (persisted in DB)
                if ((wf as any).is_published) {
                    this.isPublished = true;
                    this.executionLog = [];
                    this.showLog = true;
                    // Reconnect to active stream
                    this.workflowService.reconnectExecution(`pub_${wf.id}`)
                        .pipe(takeUntil(this.destroy$))
                        .subscribe({
                            next: (status: ExecutionStatus) => {
                                this.appendLog(status);
                                if (status.node_id !== '__done__' && status.node_id !== 'engine') {
                                    this.updateNodeStatus(status);
                                }
                                if (status.node_id === 'engine' && status.status === 'error') {
                                    this.isPublished = false;
                                }
                            },
                            complete: () => { this.isPublished = false; },
                            error: () => { this.isPublished = false; }
                        });
                }
            },
            error: () => {
                console.error('Failed to load workflow', id);
            },
        });
    }

    private resetCanvasState(): void {
        this.workflowId = '';
        this.workflowName = 'Untitled Workflow';
        this.nodes = [];
        this.links = [];
        this.nodeCounter = 0;
        this.isPublished = false;
        this.executionLog = [];
        this.showLog = false;
        this.validationError = null;
        this.sidebarNode = null;
        this.selectedNode = null;
    }

    /* ═══════════════════════════════════════════════════════════
       Node CRUD
       ═══════════════════════════════════════════════════════════ */

    addNode(typeInfo: NodeTypeInfo): void {
        if (this.isLocked) return;
        this.pushUndo();
        this.nodeCounter++;
        const id = `node_${this.nodeCounter}`;

        // Place in the center of the viewport
        const cx = (-this.panX + window.innerWidth / 2) / this.zoom - NODE_W / 2;
        const cy = (-this.panY + window.innerHeight / 2) / this.zoom - NODE_H / 2;

        const newNode: GraphNode = {
            id,
            label: `${typeInfo.label} ${this.nodeCounter}`,
            data: {
                type: typeInfo.type,
                config: { ...typeInfo.default_config },
                status: 'idle',
            },
            position: { x: cx, y: cy },
        };
        this.nodes = [...this.nodes, newNode];
    }

    addComment(): void {
        if (this.isLocked) return;
        this.pushUndo();
        this.nodeCounter++;
        const id = `comment_${this.nodeCounter}`;
        const cx = (-this.panX + window.innerWidth / 2) / this.zoom - 125;
        const cy = (-this.panY + window.innerHeight / 2) / this.zoom - 50;
        const newNode: GraphNode = {
            id, label: 'Comment',
            data: { type: 'comment' as any, config: { text: 'New Comment', width: 250, height: 100, color: '#f59e0b' }, status: 'idle' },
            position: { x: cx, y: cy }
        };
        this.nodes = [...this.nodes, newNode];
        this.saveWorkflow();
    }

    addGroup(): void {
        if (this.isLocked) return;
        this.pushUndo();
        this.nodeCounter++;
        const id = `group_${this.nodeCounter}`;
        const cx = (-this.panX + window.innerWidth / 2) / this.zoom - 200;
        const cy = (-this.panY + window.innerHeight / 2) / this.zoom - 150;
        const newNode: GraphNode = {
            id, label: 'Group',
            data: { type: 'group' as any, config: { title: 'New Group', width: 400, height: 300, collapsed: false, color: '#3b82f6' }, status: 'idle' },
            position: { x: cx, y: cy }
        };
        this.nodes = [...this.nodes, newNode];
        this.saveWorkflow();
    }

    removeNode(nodeId: string): void {
        if (this.isLocked) return;
        this.pushUndo();
        this.nodes = this.nodes.filter(n => n.id !== nodeId);
        this.links = this.links.filter(l => l.source !== nodeId && l.target !== nodeId);
        if (this.selectedNode?.id === nodeId) this.selectedNode = null;
        if (this.sidebarNode?.id === nodeId) this.sidebarNode = null;
        this.saveWorkflow(); // Auto-save
    }

    selectNode(node: GraphNode, event: MouseEvent): void {
        event.stopPropagation();
        // Clicking only visually selects (highlights) the node.
        // The config sidebar is opened via the ⚙️ button.
        this.selectedNode = node;
    }

    /** Open the config sidebar for a node (called from the ⚙️ button). */
    openSidebar(node: GraphNode, event: MouseEvent): void {
        event.stopPropagation();
        // comment and group do not have a config sidebar.
        if (node.data.type === 'comment' || node.data.type === 'group') return;
        this.sidebarNode = node;
    }

    deselectNode(): void {
        if (!this.isPanning && !this.draggingNodeTextarea) {
            this.selectedNode = null;
            this.sidebarNode = null;
        }
    }

    onNodeConfigUpdate(updatedConfig: Record<string, any>): void {
        if (this.isLocked) return;
        if (!this.sidebarNode) return;
        const idx = this.nodes.findIndex(n => n.id === this.sidebarNode!.id);
        if (idx > -1) {
            this.nodes[idx] = { ...this.nodes[idx], data: { ...this.nodes[idx].data, config: updatedConfig } };
            this.nodes = [...this.nodes];
            this.sidebarNode = this.nodes[idx];
        }
        this.saveWorkflow();
    }

    onNodeLabelUpdate(label: string): void {
        if (this.isLocked) return;
        if (!this.selectedNode) return;
        const idx = this.nodes.findIndex(n => n.id === this.selectedNode!.id);
        if (idx > -1) {
            this.nodes[idx] = { ...this.nodes[idx], label };
            this.nodes = [...this.nodes];
            this.selectedNode = this.nodes[idx];
        }
    }

    /* ═══════════════════════════════════════════════════════════
       Link CRUD
       ═══════════════════════════════════════════════════════════ */

    addLink(sourceId: string, targetId: string, sourceHandle?: string): void {
        if (this.isLocked) return;
        if (sourceId === targetId) return;
        if (this.links.some(l => l.source === sourceId && l.target === targetId && l.sourceHandle === sourceHandle)) return;
        this.pushUndo();
        this.links = [...this.links, {
            id: `edge_${sourceId}_${targetId}_${sourceHandle || 'def'}`,
            source: sourceId,
            target: targetId,
            sourceHandle: sourceHandle
        }];
    }

    removeLink(linkId: string, event: MouseEvent): void {
        event.stopPropagation();
        if (this.isLocked) return;
        this.pushUndo();
        this.links = this.links.filter(l => l.id !== linkId);
        this.saveWorkflow(); // Auto-save
    }

    /* ═══════════════════════════════════════════════════════════
       Port Positions
       ═══════════════════════════════════════════════════════════ */

    outputPortX(node: GraphNode, handle?: string): number { return (node.position?.x ?? 0) + NODE_W; }
    outputPortY(node: GraphNode, handle?: string): number {
        if (node.data.type === 'if_else') {
            return (node.position?.y ?? 0) + (handle === 'false' ? 60 : 20);
        }
        return (node.position?.y ?? 0) + NODE_H / 2;
    }
    inputPortX(node: GraphNode): number { return (node.position?.x ?? 0); }
    inputPortY(node: GraphNode): number { return (node.position?.y ?? 0) + NODE_H / 2; }

    /* ═══════════════════════════════════════════════════════════
       Visuals & Shapes
       ═══════════════════════════════════════════════════════════ */

    getShapeType(type: string | undefined): string {
        if (!type) return 'action';
        if (type === 'if_else') return 'diamond';
        if (type === 'for_loop' || type === 'while_loop') return 'loop';
        if (type === 'timer') return 'circle';
        if (type.includes('action') && !type.includes('model')) return 'llm'; // ollama_action, openai_action etc
        return 'action';
    }

    getNodeWidth(node: GraphNode): number {
        if (node.data.type === 'group') return node.data.config['width'] || 400;
        if (node.data.type === 'comment') return node.data.config['width'] || 250;
        return this.NODE_W;
    }

    getNodeHeight(node: GraphNode): number {
        if (node.data.type === 'group') return node.data.config['collapsed'] ? 48 : (node.data.config['height'] || 300);
        if (node.data.type === 'comment') return node.data.config['height'] || 100;
        return this.NODE_H;
    }

    getZIndex(node: GraphNode): number {
        if (this.selectedNode?.id === node.id) return 10;
        if (node.data.type === 'group') return 1;
        if (node.data.type === 'comment') return 2;
        return 5;
    }

    getNodesInGroup(group: GraphNode): GraphNode[] {
        const gx = group.position?.x ?? 0;
        const gy = group.position?.y ?? 0;
        const gw = group.data.config['width'] ?? 400;
        const gh = group.data.config['height'] ?? 300;
        return this.nodes.filter(n =>
            n.id !== group.id && n.data.type !== 'group' &&
            (n.position?.x ?? 0) >= gx && (n.position?.y ?? 0) >= gy &&
            (n.position?.x ?? 0) + this.NODE_W <= gx + gw &&
            (n.position?.y ?? 0) + this.NODE_H <= gy + gh
        );
    }

    get visibleNodes(): GraphNode[] {
        return this.nodes;
    }

    get visibleLinks(): GraphLink[] {
        const visibleNodeIds = new Set(this.visibleNodes.map(n => n.id));
        return this.links.filter(l => visibleNodeIds.has(l.source) && visibleNodeIds.has(l.target));
    }

    /* ═══════════════════════════════════════════════════════════
       SVG Bezier Curves
       ═══════════════════════════════════════════════════════════ */

    linkPath(link: GraphLink): string {
        const src = this.nodes.find(n => n.id === link.source);
        const tgt = this.nodes.find(n => n.id === link.target);
        if (!src || !tgt) return '';
        return this.bezier(
            this.outputPortX(src, link.sourceHandle), this.outputPortY(src, link.sourceHandle),
            this.inputPortX(tgt), this.inputPortY(tgt),
        );
    }

    tempPath(): string {
        return this.bezier(this.tempLineX1, this.tempLineY1, this.tempLineX2, this.tempLineY2);
    }

    private bezier(x1: number, y1: number, x2: number, y2: number): string {
        const dx = Math.abs(x2 - x1);
        const cp = Math.max(80, dx * 0.5);
        return `M ${x1},${y1} C ${x1 + cp},${y1} ${x2 - cp},${y2} ${x2},${y2}`;
    }

    linkMidX(link: GraphLink): number {
        const src = this.nodes.find(n => n.id === link.source);
        const tgt = this.nodes.find(n => n.id === link.target);
        if (!src || !tgt) return 0;
        return (this.outputPortX(src, link.sourceHandle) + this.inputPortX(tgt)) / 2;
    }
    linkMidY(link: GraphLink): number {
        const src = this.nodes.find(n => n.id === link.source);
        const tgt = this.nodes.find(n => n.id === link.target);
        if (!src || !tgt) return 0;
        return (this.outputPortY(src, link.sourceHandle) + this.inputPortY(tgt)) / 2;
    }

    draggingNodeTextarea = false;

    /* ═══════════════════════════════════════════════════════════
       Mouse: Node dragging
       ═══════════════════════════════════════════════════════════ */

    onNodeMouseDown(node: GraphNode, event: MouseEvent): void {
        if (event.button !== 0 || this.isLocked) return;
        event.stopPropagation();
        
        // Prevent interfering with text inputs and textarea
        if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement || (event.target as HTMLElement).tagName.toLowerCase() === 'textarea') {
            this.selectNode(node, event);
            return;
        }
        
        event.preventDefault();
        this.draggingNode = node;
        this.dragOffsetX = event.clientX / this.zoom - (node.position?.x ?? 0);
        this.dragOffsetY = event.clientY / this.zoom - (node.position?.y ?? 0);
        this.selectNode(node, event);
    }

    /* ═══════════════════════════════════════════════════════════
       Mouse: Canvas panning
       ═══════════════════════════════════════════════════════════ */

    onCanvasMouseDown(event: MouseEvent): void {
        if (event.button !== 0) return;
        if (this.draggingNode || this.isConnecting) return;
        this.isPanning = true;
        this.panStartX = event.clientX - this.panX;
        this.panStartY = event.clientY - this.panY;
    }

    connectSourceHandle: string | undefined = undefined;

    onOutputPortDown(node: GraphNode, event: MouseEvent, handle?: string): void {
        event.stopPropagation();
        if (this.isLocked) return;
        event.preventDefault();
        this.isConnecting = true;
        this.connectSourceId = node.id;
        this.connectSourceLabel = node.label;
        this.connectSourceHandle = handle;
        this.tempLineX1 = this.outputPortX(node, handle);
        this.tempLineY1 = this.outputPortY(node, handle);
        this.tempLineX2 = this.tempLineX1;
        this.tempLineY2 = this.tempLineY1;
    }

    onInputPortUp(node: GraphNode, event: MouseEvent): void {
        event.stopPropagation();
        if (this.isConnecting && this.connectSourceId && this.connectSourceId !== node.id) {
            this.addLink(this.connectSourceId, node.id, this.connectSourceHandle);
        }
        this.cancelConnecting();
    }

    cancelConnecting(): void {
        this.isConnecting = false;
        this.connectSourceId = null;
        this.connectSourceLabel = '';
    }

    /* ═══════════════════════════════════════════════════════════
       Resize handle (groups & comments)
       ═══════════════════════════════════════════════════════════ */

    onResizeHandleDown(node: GraphNode, event: MouseEvent): void {
        if (this.isLocked) return;
        event.stopPropagation();
        event.preventDefault();
        this.pushUndo();
        this.resizingNode = node;
        this.resizeStartX = event.clientX;
        this.resizeStartY = event.clientY;
        this.resizeStartW = node.data.config['width'] ?? (node.data.type === 'group' ? 400 : 250);
        this.resizeStartH = node.data.config['height'] ?? (node.data.type === 'group' ? 300 : 100);
    }

    /* ═══════════════════════════════════════════════════════════
       Global mouse handlers
       ═══════════════════════════════════════════════════════════ */

    private _dragUndoPushed = false;

    @HostListener('window:mousemove', ['$event'])
    onMouseMove(event: MouseEvent): void {
        // ── Resize handle drag ────────────────────────────────────
        if (this.resizingNode) {
            const dx = (event.clientX - this.resizeStartX) / this.zoom;
            const dy = (event.clientY - this.resizeStartY) / this.zoom;
            const newW = Math.max(200, this.resizeStartW + dx);
            const newH = Math.max(80, this.resizeStartH + dy);
            const idx = this.nodes.findIndex(n => n.id === this.resizingNode!.id);
            if (idx > -1) {
                this.nodes[idx] = {
                    ...this.nodes[idx],
                    data: {
                        ...this.nodes[idx].data,
                        config: { ...this.nodes[idx].data.config, width: newW, height: newH }
                    }
                };
                this.nodes = [...this.nodes];
            }
            return;
        }

        // ── Node drag ─────────────────────────────────────────────
        if (this.draggingNode) {
            if (!this._dragUndoPushed) {
                this.pushUndo();
                this._dragUndoPushed = true;
            }
            const x = event.clientX / this.zoom - this.dragOffsetX;
            const y = event.clientY / this.zoom - this.dragOffsetY;
            const idx = this.nodes.findIndex(n => n.id === this.draggingNode!.id);
            if (idx > -1) {
                this.nodes[idx] = { ...this.nodes[idx], position: { x, y } };
                this.nodes = [...this.nodes];
                if (this.selectedNode?.id === this.draggingNode!.id) {
                    this.selectedNode = this.nodes[idx];
                }
            }
            return;
        }

        // ── Canvas pan ────────────────────────────────────────────
        if (this.isPanning) {
            this.panX = event.clientX - this.panStartX;
            this.panY = event.clientY - this.panStartY;
            return;
        }

        // ── Connection drag ───────────────────────────────────────
        if (this.isConnecting) {
            const rect = this.canvasEl.nativeElement.getBoundingClientRect();
            this.tempLineX2 = (event.clientX - rect.left - this.panX) / this.zoom;
            this.tempLineY2 = (event.clientY - rect.top - this.panY) / this.zoom;
        }
    }

    @HostListener('window:mouseup', ['$event'])
    onMouseUp(_event: MouseEvent): void {
        if (this.resizingNode) {
            this.resizingNode = null;
            this.saveWorkflow();
        }
        if (this.draggingNode) {
            this._dragUndoPushed = false;
        }
        this.draggingNode = null;
        this.isPanning = false;
        if (this.isConnecting) this.cancelConnecting();
    }

    @HostListener('window:keydown', ['$event'])
    onKeyDown(event: KeyboardEvent): void {
        if (event.key === 'Escape') {
            this.cancelConnecting();
            this.showPalette = false;
        }
        // Undo: Ctrl+Z (not Shift)
        if ((event.ctrlKey || event.metaKey) && event.key === 'z' && !event.shiftKey) {
            event.preventDefault();
            this.undo();
        }
        // Redo: Ctrl+Shift+Z or Ctrl+Y
        if ((event.ctrlKey || event.metaKey) && ((event.key === 'z' && event.shiftKey) || event.key === 'y')) {
            event.preventDefault();
            this.redo();
        }
    }

    onWheel(event: WheelEvent): void {
        event.preventDefault();
        const delta = event.deltaY > 0 ? -0.05 : 0.05;
        this.zoom = Math.min(2, Math.max(0.3, this.zoom + delta));
    }

    /* ═══════════════════════════════════════════════════════════
       Canvas Navigation: Fit / Zoom
       ═══════════════════════════════════════════════════════════ */

    /** Animate pan + zoom so every node fits comfortably in the viewport. */
    fitToView(): void {
        const realNodes = this.nodes.filter(
            n => n.data.type !== 'comment' && n.data.type !== 'group'
        );
        // If no real nodes, include comments/groups; if still empty, just reset.
        const targets = realNodes.length > 0 ? realNodes : this.nodes;
        if (targets.length === 0) {
            this.panX = 0;
            this.panY = 0;
            this.zoom = 1;
            return;
        }

        // Compute bounding box of all target nodes
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const n of targets) {
            const x = n.position?.x ?? 0;
            const y = n.position?.y ?? 0;
            const w = this.getNodeWidth(n);
            const h = this.getNodeHeight(n);
            if (x < minX) minX = x;
            if (y < minY) minY = y;
            if (x + w > maxX) maxX = x + w;
            if (y + h > maxY) maxY = y + h;
        }

        const contentW = maxX - minX;
        const contentH = maxY - minY;

        // Available viewport (subtract some padding for the command bar and bottom toolbar)
        const el = this.canvasEl.nativeElement;
        const vpW = el.clientWidth - 100;   // 50px padding each side
        const vpH = el.clientHeight - 160;  // room for top bar + bottom toolbar

        // Scale to fit, clamped to [0.3, 1.5] — never zoom in past 150%
        const scaleX = vpW / Math.max(contentW, 1);
        const scaleY = vpH / Math.max(contentH, 1);
        const targetZoom = Math.min(1.5, Math.max(0.3, Math.min(scaleX, scaleY)));

        // Center the content in the viewport
        const centerX = (minX + maxX) / 2;
        const centerY = (minY + maxY) / 2;
        const targetPanX = (el.clientWidth / 2) - (centerX * targetZoom);
        const targetPanY = (el.clientHeight / 2) - (centerY * targetZoom);

        // Smooth animation via requestAnimationFrame
        const startPanX = this.panX;
        const startPanY = this.panY;
        const startZoom = this.zoom;
        const duration = 350; // ms
        const startTime = performance.now();

        const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);

        const animate = (now: number) => {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const ease = easeOutCubic(progress);

            this.panX = startPanX + (targetPanX - startPanX) * ease;
            this.panY = startPanY + (targetPanY - startPanY) * ease;
            this.zoom = startZoom + (targetZoom - startZoom) * ease;

            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };
        requestAnimationFrame(animate);
    }

    zoomIn(): void {
        this.zoom = Math.min(2, this.zoom + 0.15);
    }

    zoomOut(): void {
        this.zoom = Math.max(0.3, this.zoom - 0.15);
    }

    resetZoom(): void {
        this.zoom = 1;
    }

    /* ═══════════════════════════════════════════════════════════
       Workflow Build & Execution
       ═══════════════════════════════════════════════════════════ */

    private buildWorkflow(): Workflow {
        const wfNodes: WorkflowNode[] = this.nodes.map(n => ({
            id: n.id,
            type: n.data.type,
            label: n.label,
            config: n.data.config,
            position: n.position || { x: 0, y: 0 },
        }));
        const wfEdges: WorkflowEdge[] = this.links.map(l => ({
            id: l.id,
            source: l.source,
            target: l.target,
            sourceHandle: l.sourceHandle,
        }));
        return {
            id: this.workflowId || this.generateId(),
            name: this.workflowName,
            nodes: wfNodes,
            edges: wfEdges,
        };
    }

    /** BUG-01: Validate that all non-UI nodes have at least one connection. */
    private validateWorkflowConnections(): string | null {
        const executableNodes = this.nodes.filter(
            n => n.data.type !== 'comment' && n.data.type !== 'group'
        );
        if (executableNodes.length === 0) return 'Add at least one node before running.';
        if (executableNodes.length === 1) return null; // Single node is fine

        const connectedIds = new Set<string>();
        this.links.forEach(l => { connectedIds.add(l.source); connectedIds.add(l.target); });

        const disconnected = executableNodes.filter(n => !connectedIds.has(n.id));
        if (disconnected.length > 0) {
            const names = disconnected.map(n => `"${n.label}"`).join(', ');
            return `Disconnected node(s): ${names}. Connect all nodes before running.`;
        }
        return null;
    }

    executeWorkflow(): void {
        const err = this.validateWorkflowConnections();
        if (err) { this.validationError = err; setTimeout(() => this.validationError = null, 5000); return; }
        this.validationError = null;
        if (this.nodes.length === 0 || this.isExecuting) return;
        this.isExecuting = true;
        this.executionLog = [];
        this.showLog = true;
        this.resolvedThisRun.clear();

        this.nodes = this.nodes.map(n => ({
            ...n, data: { ...n.data, status: 'idle' as ExecutionStatusType, result: undefined,
                error: undefined, error_type: undefined, invalid_config_fields: undefined, duration_ms: undefined },
        }));

        const wfPayload = this.buildWorkflow();
        wfPayload.nodes = wfPayload.nodes.filter(n => n.type !== 'comment' && n.type !== 'group');
        const validIds = new Set(wfPayload.nodes.map(n => n.id));
        wfPayload.edges = wfPayload.edges.filter(e => validIds.has(e.source) && validIds.has(e.target));

        this.workflowService.executeWorkflow(wfPayload)
            .pipe(takeUntil(this.destroy$))
            .subscribe({
                next: (status: ExecutionStatus) => {
                    this.appendLog(status);
                    if (status.node_id !== '__done__' && status.node_id !== 'engine') {
                        this.updateNodeStatus(status);
                    }
                },
                error: (err: any) => { console.error('Execution error:', err); this.isExecuting = false; },
                complete: () => { this.isExecuting = false; },
            });
    }

    stopExecution(): void {
        this.workflowService.stopExecution();
        this.isExecuting = false;
        if (this.isPublished) {
            const wf = this.buildWorkflow();
            this.workflowService.unpublishWorkflow(wf.id).subscribe();
            this.isPublished = false;
        }
    }

    togglePublish(): void {
        if (this.isPublished) {
            // Unpublish
            const wf = this.buildWorkflow();
            this.workflowService.unpublishWorkflow(wf.id)
                .subscribe({
                    next: () => { this.isPublished = false; },
                    error: (err: any) => console.error('Unpublish failed:', err),
                });
        } else {
            const validErr = this.validateWorkflowConnections();
            if (validErr) { this.validationError = validErr; setTimeout(() => this.validationError = null, 5000); return; }
            this.validationError = null;
            // Save first, then publish
            const wf = this.buildWorkflow();
            this.workflowService.saveWorkflow(wf).subscribe({
                next: () => {
                    this.workflowService.publishWorkflow(wf.id)
                        .subscribe({
                            next: (res: any) => {
                                this.isPublished = true;
                                this.executionLog = [];
                                this.showLog = true;
                                // Optionally stream live updates
                                if (res.exec_id) {
                                    this.workflowService.reconnectExecution(res.exec_id)
                                        .pipe(takeUntil(this.destroy$))
                                        .subscribe({
                                            next: (status: ExecutionStatus) => {
                                                this.appendLog(status);
                                                if (status.node_id !== '__done__' && status.node_id !== 'engine') {
                                                    this.updateNodeStatus(status);
                                                }
                                                // If engine reports error, publish has stopped
                                                if (status.node_id === 'engine' && (status.status === 'error' || status.status === 'failed')) {
                                                    this.isPublished = false;
                                                }
                                            },
                                            complete: () => { this.isPublished = false; },
                                            error: () => { this.isPublished = false; },
                                        });
                                }
                            },
                            error: (err: any) => console.error('Publish failed:', err),
                        });
                },
            });
        }
    }

    private updateNodeStatus(statusEvent: ExecutionStatus): void {
        const nodeId = statusEvent.node_id;
        const status = statusEvent.status as ExecutionStatusType;

        // ── Loop-body skip overwrite fix ──────────────────────────────
        // The graph navigator pre-marks loop body nodes as skipped, then the
        // loop runner sends real running→success events. After the loop, the
        // outer cursor walks past those same node IDs again and emits a trailing
        // `skipped`. Guard: once a node has reached a terminal status this run,
        // ignore any subsequent `skipped` event for it.
        const terminalStatuses: ExecutionStatusType[] = ['success', 'failed', 'error', 'complete'];
        if (status === 'skipped' && this.resolvedThisRun.has(nodeId)) {
            return;
        }
        if (terminalStatuses.includes(status)) {
            this.resolvedThisRun.add(nodeId);
        }
        // ─────────────────────────────────────────────────────────────

        const idx = this.nodes.findIndex(n => n.id === nodeId);
        if (idx > -1) {
            const isRunning = status === 'running';
            // Clear all ephemeral execution state when a node starts re-running
            const updatedData = {
                ...this.nodes[idx].data,
                status,
                result:               isRunning ? undefined : statusEvent.result,
                error:                isRunning ? undefined : (statusEvent.error || statusEvent.message || undefined),
                error_type:           isRunning ? undefined : (statusEvent.error_type || undefined),
                invalid_config_fields: isRunning ? undefined : (statusEvent.invalid_config_fields?.length ? statusEvent.invalid_config_fields : undefined),
                duration_ms:          isRunning ? undefined : (statusEvent.result?.['__meta__']?.duration_ms ?? undefined),
            };
            this.nodes[idx] = { ...this.nodes[idx], data: updatedData };
            this.nodes = [...this.nodes];
            if (this.selectedNode?.id === nodeId) this.selectedNode = this.nodes[idx];
            if (this.sidebarNode?.id === nodeId) this.sidebarNode = this.nodes[idx];
        }
    }

    /* ── Node Error Accessors (used by canvas template) ──────────── */

    getNodeError(node: GraphNode): string {
        return node.data.error || '';
    }

    getNodeErrorType(node: GraphNode): string {
        return node.data.error_type || '';
    }

    getNodeInvalidFields(node: GraphNode): string[] {
        return node.data.invalid_config_fields || [];
    }

    getNodeDuration(node: GraphNode): string {
        const ms = node.data.duration_ms;
        if (ms === undefined || ms === null) return '';
        if (ms < 1000) return `${Math.round(ms)}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    }

    /** Format a log entry's duration from __meta__.duration_ms in the result */
    getLogEntryDuration(entry: ExecutionStatus): string {
        const ms = (entry.result as any)?.['__meta__']?.duration_ms;
        if (!ms) return '';
        if (ms < 1000) return `${Math.round(ms)}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    }

    /** Append a log entry and enforce the 500-entry cap to prevent memory growth. */
    private appendLog(status: ExecutionStatus): void {
        const MAX_LOG = 500;
        const next = [...this.executionLog, status];
        this.executionLog = next.length > MAX_LOG ? next.slice(next.length - MAX_LOG) : next;
    }

    saveWorkflow(): void {
        if (this._saveTimer) clearTimeout(this._saveTimer);
        this._saveTimer = setTimeout(() => {
            this._saveTimer = null;
            this._doSave();
        }, 400);
    }

    private _doSave(): void {
        const wf = this.buildWorkflow();
        this.workflowService.saveWorkflow(wf).pipe(
            takeUntil(this.destroy$)
        ).subscribe({
            next: (res: any) => {
                this.workflowId = wf.id;
                if (!this.route.snapshot.params['id']) {
                    this.router.navigate(['/editor', wf.id]);
                }
            },
            error: (err: any) => console.error('Save failed:', err),
        });
    }

    /* ═══════════════════════════════════════════════════════════
       Helpers
       ═══════════════════════════════════════════════════════════ */

    getNodeAccentClass(type: NodeType | undefined): string {
        if (!type) return '';
        return ({
            http_request: 'node-accent-http',
            model_action: 'node-accent-model',
            ollama_action: 'node-accent-agent',
            openai_action: 'node-accent-agent',
            gemini_action: 'node-accent-agent',
            openrouter_action: 'node-accent-agent',
            if_else: 'node-logic-diamond',
            for_loop: 'node-logic-loop',
            while_loop: 'node-logic-loop',
            send_email: 'node-accent-email',
            receive_email: 'from-emerald-600 to-teal-500',
            discord_send_attachment: 'from-indigo-600 to-violet-500',
            telegram_send_document: 'from-sky-500 to-cyan-400',
            timer: 'node-accent-timer',
            timer_cron: 'from-amber-600 to-orange-500',
            web_scrape: 'from-purple-700 to-violet-600',
            web_search_scrape: 'from-blue-600 to-cyan-500',
            webhook_trigger: 'from-violet-600 to-purple-500',
            telegram_trigger: 'from-sky-600 to-blue-500',
        } as Record<string, string>)[type] || '';
    }

    getNodeIcon(type: NodeType | undefined): string {
        if (!type) return '📦';
        return ({
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
            discord_send_attachment: '📎',
            telegram_send_document: '📎',
            timer: '⏰',
            timer_cron: '⏰',
            web_scrape: '🕷️',
            web_search_scrape: '🔍',
            webhook_trigger: '🔗',
            telegram_trigger: '💬',
        } as Record<string, string>)[type] || '📦';
    }

    getStatusIcon(status: ExecutionStatusType): string {
        return ({
            idle: '⏸️', running: '⚡', success: '✅', error: '❌',
            failed: '❌', complete: '🎉', skipped: '⏭️',
        })[status] || '⏸️';
    }

    getNodeSubtitle(node: GraphNode): string {
        const cfg = node.data?.config || {};
        switch (node.data?.type) {
            case 'timer_cron':
                return cfg['recurrence'] && cfg['trigger_time']
                    ? `${cfg['recurrence']} @ ${cfg['trigger_time']}`
                    : 'Configure schedule...';
            case 'web_scrape':
                return cfg['url']
                    ? (cfg['url'].length > 35 ? cfg['url'].substring(0, 35) + '…' : cfg['url'])
                    : 'No URL set';
            case 'web_search_scrape':
                return cfg['query']
                    ? (cfg['query'].length > 35 ? cfg['query'].substring(0, 35) + '…' : cfg['query'])
                    : 'No query set';
            case 'receive_email':
                const folder = cfg['folder'] || 'INBOX';
                const mode = cfg['subject_filter_mode'] || 'none';
                const val = cfg['subject_filter_value'] || '';
                return mode === 'none' 
                    ? `Listening: ${folder}` 
                    : `Listening: ${folder} (Filter: ${val})`;
            default: return '';
        }
    }

    togglePalette(): void { this.showPalette = !this.showPalette; }
    toggleLog(): void { this.showLog = !this.showLog; }
    
    toggleAiChat(): void {
        this.showAiChat = !this.showAiChat;
        if (this.showAiChat) {
            setTimeout(() => {
                const el = document.getElementById('ai-chat-input');
                if (el) el.focus();
            }, 50);
        }
    }

    buildWorkflowFromAi(): void {
        if (!this.aiPrompt.trim() || this.isAiBuilding) return;
        this.isAiBuilding = true;
        this.aiBuildProgress = 0;
        this.validationError = null;
        this.aiWarningBanner = null;
        this.aiGenState = 'generating';
        this.aiGenMessage = 'Starting AI generation...';

        this.aiService.generateNode(this.aiPrompt).subscribe({
            next: (event: AiGenEvent) => {
                this.aiGenMessage = event.message;

                // Map stages to state machine
                switch (event.stage) {
                    case 'spec':
                    case 'code':
                        this.aiGenState = 'generating';
                        this.aiBuildProgress = event.stage === 'spec' ? 20 : 40;
                        break;
                    case 'validate':
                        this.aiGenState = 'validating';
                        this.aiBuildProgress = 60;
                        break;
                    case 'deploy':
                    case 'frontend':
                        this.aiGenState = 'deploying';
                        this.aiBuildProgress = event.stage === 'deploy' ? 80 : 90;
                        break;
                    case 'done':
                        this.aiGenState = 'done';
                        this.aiBuildProgress = 100;
                        break;
                    case 'error':
                        this.aiGenState = 'error';
                        break;
                }
            },
            error: (err: Error) => {
                this.isAiBuilding = false;
                this.aiGenState = 'error';
                this.aiGenMessage = err.message;
                this.validationError = 'AI Error: ' + err.message;
                setTimeout(() => {
                    this.validationError = null;
                    this.aiGenState = 'idle';
                }, 5000);
            },
            complete: () => {
                this.isAiBuilding = false;
                this.aiGenState = 'done';
                this.aiPrompt = '';
                this.aiGenMessage = 'Node generated successfully!';
                setTimeout(() => {
                    this.aiGenState = 'idle';
                    this.aiGenMessage = '';
                }, 3000);
            },
        });
    }

    toggleGroup(node: GraphNode, event: MouseEvent): void {
        event.stopPropagation();
        const collapsed = !node.data.config['collapsed'];
        this.onNodeConfigUpdate({ ...node.data.config, collapsed });
    }

    /* ═══════════════════════════════════════════════════════════
       JSON Import / Export
       ═══════════════════════════════════════════════════════════ */

    /**
     * Strip fields marked as secret from all node configs.
     * Uses config_schema from the catalog to identify which fields are secrets.
     */
    private stripSecrets(wf: Workflow): Workflow {
        const cleaned = JSON.parse(JSON.stringify(wf)) as Workflow;
        for (const node of cleaned.nodes) {
            const typeInfo = this.allNodeTypes.find(t => t.type === node.type);
            if (!typeInfo?.config_schema) continue;

            const secretKeys = new Set<string>();

            if (Array.isArray(typeInfo.config_schema)) {
                // List-based schema
                for (const field of typeInfo.config_schema) {
                    if (field.secret) secretKeys.add(field.key);
                }
            } else if (typeof typeInfo.config_schema === 'object') {
                // Dict-based schema
                for (const [key, def] of Object.entries(typeInfo.config_schema)) {
                    if (typeof def === 'object' && (def as any).secret) secretKeys.add(key);
                }
            }

            for (const key of secretKeys) {
                if (key in node.config) {
                    delete node.config[key];
                }
            }
        }
        return cleaned;
    }

    exportWorkflow(): void {
        const wf = this.stripSecrets(this.buildWorkflow());
        const exportData = {
            workflow: wf,
            export_version: '1.0',
            engine: 'M8M',
        };
        const json = JSON.stringify(exportData, null, 2);

        // Copy to clipboard
        navigator.clipboard.writeText(json).then(() => {
            console.log('Workflow JSON copied to clipboard (secrets stripped)');
        });

        // Download as file
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${wf.name.replace(/\s+/g, '_')}_workflow.json`;
        a.click();
        URL.revokeObjectURL(url);
    }

    copyWorkflowJson(): void {
        const wf = this.stripSecrets(this.buildWorkflow());
        const json = JSON.stringify(wf, null, 2);
        navigator.clipboard.writeText(json).then(() => {
            this.showCopyToast('Workflow JSON copied!');
        });
    }

    /* ═══════════════════════════════════════════════════════════
       Copy Execution Results
       ═══════════════════════════════════════════════════════════ */

    /** Visible only after execution finishes (not mid-run, not while published). */
    get hasExecutionResults(): boolean {
        return !this.isExecuting && !this.isPublished && this.executionLog.length > 0;
    }

    /** Show a brief toast message below the command bar. */
    private showCopyToast(message: string): void {
        if (this._copyToastTimer) clearTimeout(this._copyToastTimer);
        this.copyToast = message;
        this._copyToastTimer = setTimeout(() => {
            this.copyToast = null;
            this._copyToastTimer = null;
        }, 2500);
    }

    /**
     * Truncate a result object to maxBytes when serialized.
     * Returns the original if small enough, otherwise a truncated
     * copy with a `_truncated: true` flag.
     */
    private truncateResult(result: any, maxBytes = 10240): any {
        if (result == null) return null;
        try {
            const json = JSON.stringify(result);
            if (json.length <= maxBytes) return result;
            return { _truncated: true, _original_size: json.length, preview: json.substring(0, maxBytes) };
        } catch {
            return { _truncated: true, _error: 'Unserializable result' };
        }
    }

    /**
     * For published workflows, the execution log is one flat stream
     * spanning many cooldown cycles. Each cycle starts with an
     * `engine / running` entry. Slice to the most recent cycle.
     * For one-off executions, returns all real-node log entries.
     */
    private getLastRunLogs(): ExecutionStatus[] {
        const SYNTHETIC_IDS = new Set(['__done__', 'engine']);

        // Check if this is/was a published workflow
        const hasEngineEntries = this.executionLog.some(e => e.node_id === 'engine');

        if (hasEngineEntries) {
            // Find the index of the last engine "running" entry (cycle start marker)
            let lastCycleStart = -1;
            for (let i = this.executionLog.length - 1; i >= 0; i--) {
                const e = this.executionLog[i];
                if (e.node_id === 'engine' && e.status === 'running') {
                    lastCycleStart = i;
                    break;
                }
            }
            // Slice from that boundary, then filter out synthetic entries
            const sliced = lastCycleStart >= 0
                ? this.executionLog.slice(lastCycleStart)
                : this.executionLog;
            return sliced.filter(e => !SYNTHETIC_IDS.has(e.node_id));
        }

        // One-off execution: just filter out synthetics
        return this.executionLog.filter(e => !SYNTHETIC_IDS.has(e.node_id));
    }

    /**
     * Build a structured JSON report of the last execution run.
     *
     * - Excludes comment/group nodes from all counts
     * - Pulls input from node config (secrets stripped)
     * - Pulls output from node.data.result (truncated to 10KB)
     * - Cross-references error details from executionLog (not node state)
     * - Skipped = explicitly skipped by GraphNavigator OR still idle after run
     */
    buildExecutionReport(): object {
        const execNodes = this.nodes.filter(
            n => n.data.type !== 'comment' && n.data.type !== 'group'
        );

        const lastRunLogs = this.getLastRunLogs();

        // Build a map: node_id → last terminal log entry (for error details)
        const logByNode = new Map<string, ExecutionStatus>();
        for (const entry of lastRunLogs) {
            // Keep overwriting — last entry wins (running → success, or running → failed)
            logByNode.set(entry.node_id, entry);
        }

        // Strip secrets from a workflow copy for safe input extraction
        const cleanWf = this.stripSecrets(this.buildWorkflow());
        const cleanConfigMap = new Map<string, Record<string, any>>();
        for (const n of cleanWf.nodes) {
            cleanConfigMap.set(n.id, n.config);
        }

        const executedNodes: any[] = [];
        const skippedNodes: any[] = [];

        for (const node of execNodes) {
            const status = node.data.status;
            const logEntry = logByNode.get(node.id);

            if (status === 'skipped' || status === 'idle') {
                skippedNodes.push({
                    node_id: node.id,
                    label: node.label,
                    type: node.data.type,
                });
                continue;
            }

            const entry: any = {
                node_id: node.id,
                label: node.label,
                type: node.data.type,
                status: status,
                input: cleanConfigMap.get(node.id) || {},
                output: this.truncateResult(node.data.result),
            };

            // Error details live on the log entry, not on GraphNode.data
            if ((status === 'failed' || status === 'error') && logEntry) {
                entry.error = logEntry.error || logEntry.message || null;
                entry.error_type = logEntry.error_type || null;
                if (logEntry.invalid_config_fields?.length) {
                    entry.invalid_config_fields = logEntry.invalid_config_fields;
                }
            }

            executedNodes.push(entry);
        }

        const succeeded = executedNodes.filter(n => n.status === 'success').length;
        const failed = executedNodes.filter(n => n.status === 'failed' || n.status === 'error').length;

        return {
            workflow: this.workflowName,
            workflow_id: this.workflowId,
            timestamp: new Date().toISOString(),
            engine: 'M8M',
            summary: {
                total_nodes: execNodes.length,
                executed: executedNodes.length,
                succeeded,
                failed,
                skipped: skippedNodes.length,
            },
            executed_nodes: executedNodes,
            skipped_nodes: skippedNodes,
        };
    }

    copyExecutionResults(): void {
        const report = this.buildExecutionReport();
        const json = JSON.stringify(report, null, 2);
        navigator.clipboard.writeText(json).then(() => {
            this.showCopyToast('Execution report copied!');
        });
    }

    triggerImport(): void {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.json';
        input.onchange = (event: any) => {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (e: any) => {
                try {
                    const data = JSON.parse(this.stripJsonComments(e.target.result));
                    const wf = this.normalizeImportedWorkflow(data.workflow || data); // Support both wrapped and raw
                    this.loadFromJson(wf);
                } catch (err) {
                    console.error('Invalid JSON file:', err);
                }
            };
            reader.readAsText(file);
        };
        input.click();
    }

    private stripJsonComments(raw: string): string {
        let output = '';
        let inString = false;
        let quote = '';
        let escaped = false;
        for (let i = 0; i < raw.length; i++) {
            const ch = raw[i];
            const next = raw[i + 1] || '';
            if (inString) {
                output += ch;
                if (escaped) escaped = false;
                else if (ch === '\\') escaped = true;
                else if (ch === quote) { inString = false; quote = ''; }
                continue;
            }
            if (ch === '"' || ch === "'") {
                inString = true;
                quote = ch;
                output += ch;
                continue;
            }
            if (ch === '/' && next === '/') {
                i += 2;
                while (i < raw.length && raw[i] !== '\n' && raw[i] !== '\r') i++;
                i--;
                continue;
            }
            if (ch === '/' && next === '*') {
                i += 2;
                while (i + 1 < raw.length && !(raw[i] === '*' && raw[i + 1] === '/')) i++;
                i++;
                continue;
            }
            output += ch;
        }
        return output;
    }

    private normalizeImportedWorkflow(wf: any): any {
        return wf;
    }

    private loadFromJson(wf: any): void {
        this.workflowName = wf.name || 'Imported Workflow';
        this.workflowId = wf.id || this.generateId();
        this.nodes = (wf.nodes || []).map((n: any) => ({
            id: n.id,
            label: n.label,
            data: { type: n.type, config: n.config || {}, status: 'idle' as ExecutionStatusType },
            position: n.position || { x: 0, y: 0 },
        }));
        this.links = (wf.edges || []).map((e: any) => ({
            id: e.id,
            source: e.source,
            target: e.target,
            sourceHandle: e.sourceHandle,
        }));
        const suffixes = this.nodes
            .map(n => parseInt(n.id.split('_')[1]))
            .filter(n => !isNaN(n));
        this.nodeCounter = suffixes.length > 0 ? Math.max(...suffixes) : 0;
        this.executionLog = [];
        this.selectedNode = null;
    }

    private generateId(): string { return Math.random().toString(36).substring(2, 10); }

    trackByNode(index: number, node: GraphNode): string {
        return node.id;
    }

    /* ═══════════════════════════════════════════════════════════
       Edge Labels (ISSUE 1)
       ═══════════════════════════════════════════════════════════ */

    /**
     * Returns the label shown on a canvas edge.
     * For if_else source handles, prefixes with ✓/✗ to distinguish branches.
     * Always shows the target node's label so users can read the graph without hovering.
     */
    getLinkLabel(link: GraphLink): string {
        const target = this.nodes.find(n => n.id === link.target);
        const targetLabel = target?.label ?? link.target;
        if (link.sourceHandle === 'true')  return `✓ ${targetLabel}`;
        if (link.sourceHandle === 'false') return `✗ ${targetLabel}`;
        return targetLabel;
    }

    /* ═══════════════════════════════════════════════════════════
       Sidebar Config Change (ISSUE 4)
       ═══════════════════════════════════════════════════════════ */

    onSidebarConfigChange(event: { key: string; value: any }): void {
        if (!this.sidebarNode) return;
        const idx = this.nodes.findIndex(n => n.id === this.sidebarNode!.id);
        if (idx === -1) return;
        // Update the authoritative nodes array — preserves across status events
        this.nodes[idx].data.config[event.key] = event.value;
        // Trigger debounced save
        this.saveWorkflow();
    }

    /* ═══════════════════════════════════════════════════════════
       Contrast Color (ISSUE 5)
       ═══════════════════════════════════════════════════════════ */

    /**
     * Returns '#000000' or '#ffffff' depending on which has higher contrast
     * against the given hex background colour.
     * Uses the WCAG relative luminance formula.
     */
    contrastColor(hex: string): string {
        const clean = (hex || '#f59e0b').replace('#', '');
        const full  = clean.length === 3
            ? clean.split('').map(c => c + c).join('')
            : clean.padEnd(6, '0');

        const r = parseInt(full.slice(0, 2), 16) / 255;
        const g = parseInt(full.slice(2, 4), 16) / 255;
        const b = parseInt(full.slice(4, 6), 16) / 255;

        // WCAG linearise each channel
        const lin = (c: number) => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        const L   = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);

        // L > 0.179 means the background is perceived as light → use black text
        return L > 0.179 ? '#000000' : '#ffffff';
    }

    /* ═══════════════════════════════════════════════════════════
       Undo / Redo
       ═══════════════════════════════════════════════════════════ */

    get canUndo(): boolean { return this.undoStack.length > 0; }
    get canRedo(): boolean { return this.redoStack.length > 0; }

    /** Snapshot current nodes + links state onto the undo stack. */
    pushUndo(): void {
        const snapshot = JSON.stringify({
            nodes: this.nodes.map(n => ({ ...n, data: { ...n.data, config: { ...n.data.config } } })),
            links: [...this.links]
        });
        // Don't push duplicates (e.g. rapid identical calls)
        if (this.undoStack.length > 0 && this.undoStack[this.undoStack.length - 1] === snapshot) return;
        this.undoStack.push(snapshot);
        if (this.undoStack.length > this.MAX_UNDO) this.undoStack.shift();
        this.redoStack = [];
    }

    undo(): void {
        if (!this.canUndo) return;
        // Save current state to redo
        const current = JSON.stringify({ nodes: this.nodes, links: this.links });
        this.redoStack.push(current);
        // Restore previous state
        const prev = JSON.parse(this.undoStack.pop()!);
        this.nodes = prev.nodes;
        this.links = prev.links;
        this.selectedNode = null;
        this.sidebarNode = null;
        this.saveWorkflow();
    }

    redo(): void {
        if (!this.canRedo) return;
        // Save current state to undo
        const current = JSON.stringify({ nodes: this.nodes, links: this.links });
        this.undoStack.push(current);
        // Restore next state
        const next = JSON.parse(this.redoStack.pop()!);
        this.nodes = next.nodes;
        this.links = next.links;
        this.selectedNode = null;
        this.sidebarNode = null;
        this.saveWorkflow();
    }
}
