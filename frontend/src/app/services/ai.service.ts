import { Injectable, OnDestroy } from '@angular/core';
import { Observable, Subject, Subscriber } from 'rxjs';

/**
 * Typed SSE event from the backend AI generation pipeline.
 */
export interface AiGenEvent {
  stage: 'spec' | 'code' | 'validate' | 'deploy' | 'frontend' | 'done' | 'error' | 'keepalive';
  message: string;
  status: 'running' | 'done' | 'error';
}

/**
 * AiService — Angular service for AI node generation via SSE.
 *
 * Manages EventSource lifecycle to prevent memory leaks.
 * Implements OnDestroy to auto-close connections on component teardown.
 */
@Injectable({ providedIn: 'root' })
export class AiService implements OnDestroy {
  private activeSource: EventSource | null = null;
  private destroy$ = new Subject<void>();

  /**
   * Start AI node generation and stream progress events.
   *
   * 1. POST /api/v1/ai/generate-node → { job_id }
   * 2. Connect EventSource to /api/v1/jobs/{job_id}/stream
   * 3. Emit AiGenEvent objects until done/error
   */
  generateNode(prompt: string): Observable<AiGenEvent> {
    // Close any existing connection
    this.closeStream();

    return new Observable<AiGenEvent>((subscriber: Subscriber<AiGenEvent>) => {
      // Step 1: Start the generation job
      fetch('/api/v1/ai/generate-node', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data: { job_id: string }) => {
          // Step 2: Connect SSE
          const source = new EventSource(`/api/v1/jobs/${data.job_id}/stream`);
          this.activeSource = source;

          source.onmessage = (event) => {
            try {
              const parsed: AiGenEvent = JSON.parse(event.data);

              // Skip keepalive events
              if (parsed.stage === 'keepalive') return;

              subscriber.next(parsed);

              // Complete on terminal status
              if (parsed.status === 'done' || parsed.status === 'error') {
                source.close();
                this.activeSource = null;
                if (parsed.status === 'error') {
                  subscriber.error(new Error(parsed.message));
                } else {
                  subscriber.complete();
                }
              }
            } catch (e) {
              console.error('[AiService] Failed to parse SSE event:', e);
            }
          };

          source.onerror = (err) => {
            console.error('[AiService] SSE connection error:', err);
            source.close();
            this.activeSource = null;
            subscriber.error(new Error('SSE connection lost'));
          };
        })
        .catch((err) => {
          subscriber.error(err);
        });

      // Cleanup on unsubscribe
      return () => {
        this.closeStream();
      };
    });
  }

  /**
   * Close the active EventSource connection.
   */
  closeStream(): void {
    if (this.activeSource) {
      this.activeSource.close();
      this.activeSource = null;
    }
  }

  ngOnDestroy(): void {
    this.closeStream();
    this.destroy$.next();
    this.destroy$.complete();
  }
}
