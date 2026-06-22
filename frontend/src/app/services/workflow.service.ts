import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, Subject, interval, takeWhile, switchMap, of, catchError } from 'rxjs';
import {
    Workflow,
    ExecutionStatus,
    NodeTypeInfo,
} from '../models/workflow.models';

@Injectable({ providedIn: 'root' })
export class WorkflowService {
    // Empty string → relative URL → routed through nginx (prod) or ng serve proxy (dev).
    // proxy.conf.json (dev) and nginx.conf (prod) both already handle /api/ and /ws/.
    private readonly API_BASE = '';
    private readonly WS_BASE  = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}`;
    private ws: WebSocket | null = null;
    private reconnectAttempts = 0;
    private readonly MAX_RECONNECT_ATTEMPTS = 3;

    /** Current execution ID (persists across page loads if stored) */
    currentExecId: string | null = null;

    /** Stream of execution status updates */
    private executionStatus$ = new Subject<ExecutionStatus>();

    constructor(private http: HttpClient) { }

    /* ─── REST API ────────────────────────────────────────────────── */

    getNodeTypes(): Observable<NodeTypeInfo[]> {
        return this.http.get<NodeTypeInfo[]>(`${this.API_BASE}/api/node-types`);
    }

    saveWorkflow(workflow: Workflow): Observable<{ id: string; message: string }> {
        return this.http.post<{ id: string; message: string }>(
            `${this.API_BASE}/api/workflows`,
            workflow
        );
    }

    listWorkflows(): Observable<Workflow[]> {
        return this.http.get<Workflow[]>(`${this.API_BASE}/api/workflows`);
    }

    getWorkflow(id: string): Observable<Workflow> {
        return this.http.get<Workflow>(`${this.API_BASE}/api/workflows/${id}`);
    }

    deleteWorkflow(id: string): Observable<{ message: string }> {
        return this.http.delete<{ message: string }>(
            `${this.API_BASE}/api/workflows/${id}`
        );
    }

    publishWorkflow(workflowId: string): Observable<any> {
        return this.http.post<any>(`${this.API_BASE}/api/workflows/${workflowId}/publish`, {});
    }

    unpublishWorkflow(workflowId: string): Observable<any> {
        return this.http.post<any>(`${this.API_BASE}/api/workflows/${workflowId}/unpublish`, {});
    }

    uploadModel(file: File): Observable<{ path: string }> {
        const formData = new FormData();
        formData.append('file', file);
        return this.http.post<{ path: string }>(`${this.API_BASE}/api/models/upload`, formData);
    }

    buildWorkflow(prompt: string, context?: any): Observable<any> {
        return this.http.post<any>(`${this.API_BASE}/api/ai/build-workflow`, {
            prompt,
            context
        });
    }

    /* ─── Execution API (Background) ─────────────────────────────── */

    /**
     * Start a background execution via REST, then stream logs via WebSocket.
     * Execution continues on the server even if the WebSocket disconnects.
     */
    executeWorkflow(workflow: Workflow): Observable<ExecutionStatus> {
        this.disconnectWs();
        const subject = new Subject<ExecutionStatus>();
        let completed = false;

        this.ws = new WebSocket(`${this.WS_BASE}/ws/execute`);

        this.ws.onopen = () => {
            this.ws!.send(JSON.stringify({
                id: workflow.id,
                name: workflow.name,
                nodes: workflow.nodes,
                edges: workflow.edges,
            }));
        };

        this.ws.onmessage = (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                if (data?.type === 'keepalive' || data?.status === 'keepalive') return;

                // Capture exec_id from the startup message
                if (data.exec_id && data.status === 'started') {
                    this.currentExecId = data.exec_id;
                    this.reconnectAttempts = 0;
                    return;
                }

                const status: ExecutionStatus = data;
                subject.next(status);
                this.executionStatus$.next(status);

                if (status.node_id === '__done__' || status.status === 'complete') {
                    completed = true;
                    subject.complete();
                }
            } catch (e) {
                console.error('Failed to parse WS message:', e);
            }
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket error:', err);
            // On WS error, try polling for results if we have an exec_id
            if (this.currentExecId) {
                this.pollExecution(this.currentExecId).subscribe({
                    next: (status) => { subject.next(status); this.executionStatus$.next(status); },
                    complete: () => subject.complete(),
                    error: (e) => subject.error(e),
                });
            } else {
                subject.error(err);
            }
        };

        this.ws.onclose = () => {
            if (!completed && this.currentExecId && this.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
                this.reconnectAttempts += 1;
                setTimeout(() => {
                    this.reconnectExecution(this.currentExecId!).subscribe({
                        next: (status) => { subject.next(status); this.executionStatus$.next(status); },
                        complete: () => subject.complete(),
                        error: (e) => subject.error(e),
                    });
                }, 750 * this.reconnectAttempts);
                return;
            }
            subject.complete();
        };

        return subject.asObservable();
    }

    /**
     * Reconnect to a running execution by exec_id to stream live updates.
     */
    reconnectExecution(execId: string): Observable<ExecutionStatus> {
        this.disconnectWs();
        const subject = new Subject<ExecutionStatus>();
        let completed = false;

        this.ws = new WebSocket(`${this.WS_BASE}/ws/executions/${execId}`);
        this.currentExecId = execId;

        this.ws.onmessage = (event: MessageEvent) => {
            try {
                const status: ExecutionStatus = JSON.parse(event.data);
                if ((status as any)?.type === 'keepalive' || status.status === 'keepalive' as any) return;
                subject.next(status);
                this.executionStatus$.next(status);

                if (status.node_id === '__done__' || status.status === 'complete') {
                    completed = true;
                    subject.complete();
                }
            } catch (e) {
                console.error('Failed to parse WS message:', e);
            }
        };

        this.ws.onerror = () => {
            if (this.currentExecId) {
                this.pollExecution(this.currentExecId).subscribe({
                    next: (status) => { subject.next(status); },
                    complete: () => subject.complete(),
                });
            }
        };

        this.ws.onclose = () => {
            if (!completed && this.currentExecId && this.reconnectAttempts < this.MAX_RECONNECT_ATTEMPTS) {
                this.reconnectAttempts += 1;
                setTimeout(() => {
                    this.reconnectExecution(this.currentExecId!).subscribe({
                        next: (status) => { subject.next(status); this.executionStatus$.next(status); },
                        complete: () => subject.complete(),
                        error: (e) => subject.error(e),
                    });
                }, 750 * this.reconnectAttempts);
                return;
            }
            subject.complete();
        };

        return subject.asObservable();
    }

    /**
     * Poll execution status via REST (fallback when WebSocket is unavailable).
     */
    pollExecution(execId: string): Observable<ExecutionStatus> {
        const subject = new Subject<ExecutionStatus>();

        this.http.get<any>(`${this.API_BASE}/api/executions/${execId}`).subscribe({
            next: (detail) => {
                // Emit all logs
                if (detail.logs) {
                    for (const log of detail.logs) {
                        subject.next(log as ExecutionStatus);
                    }
                }
                // Send done signal
                subject.next({
                    node_id: '__done__',
                    node_label: 'Execution Complete',
                    status: 'complete',
                    result: { message: `Execution ${detail.state}`, exec_id: execId, state: detail.state },
                } as any);
                subject.complete();
            },
            error: (err) => subject.error(err),
        });

        return subject.asObservable();
    }

    /** Get global stream of execution status updates */
    getExecutionUpdates(): Observable<ExecutionStatus> {
        return this.executionStatus$.asObservable();
    }

    /** Send a stop signal to the backend */
    stopExecution(): void {
        // Try WebSocket first
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'stop' }));
        }
        // Also try REST as backup
        if (this.currentExecId) {
            this.http.post(`${this.API_BASE}/api/executions/${this.currentExecId}/stop`, {}).subscribe();
        }
    }

    /** Disconnect WebSocket */
    disconnectWs(): void {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
