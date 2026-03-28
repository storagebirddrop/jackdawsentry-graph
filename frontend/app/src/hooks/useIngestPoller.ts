/**
 * useIngestPoller — polls the ingest status endpoint for a single pending node.
 *
 * Called from the render-null <IngestPoller> component so that React's
 * rules-of-hooks are respected even when the set of pending nodes is dynamic.
 *
 * Polling stops when:
 *  - status becomes 'completed'   → onComplete(nodeId) is called
 *  - status becomes 'failed'      → onTimeout(nodeId) is called
 *  - status becomes 'not_found'   → onUnavailable(nodeId) is called
 *  - TIMEOUT_MS elapses           → onTimeout(nodeId) is called
 *
 * Network / HTTP errors do not stop polling — they are treated as transient.
 */

import { useEffect } from 'react';
import { getIngestStatus } from '../api/client';

const POLL_INTERVAL_MS = 5_000;
const TIMEOUT_MS = 3 * 60 * 1_000; // 3 minutes

export function useIngestPoller(
  sessionId: string,
  nodeId: string,
  address: string,
  chain: string,
  onComplete: (nodeId: string) => void,
  onUnavailable: (nodeId: string) => void,
  onTimeout: (nodeId: string) => void,
): void {
  useEffect(() => {
    const startedAt = Date.now();
    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout> | null = null;

    async function poll(): Promise<void> {
      if (cancelled) return;

      if (Date.now() - startedAt >= TIMEOUT_MS) {
        onTimeout(nodeId);
        return;
      }

      try {
        const status = await getIngestStatus(sessionId, address, chain);
        if (cancelled) return;

        if (status.status === 'completed') {
          onComplete(nodeId);
          return;
        }

        if (status.status === 'failed') {
          // Terminal failure — stop polling and surface it.
          onTimeout(nodeId);
          return;
        }

        if (status.status === 'not_found') {
          onUnavailable(nodeId);
          return;
        }

        // 'pending' | 'running' → keep polling
      } catch {
        // Network / HTTP error — keep polling (transient)
      }

      if (!cancelled) {
        timerId = setTimeout(() => void poll(), POLL_INTERVAL_MS);
      }
    }

    void poll();

    return () => {
      cancelled = true;
      if (timerId !== null) clearTimeout(timerId);
    };
  }, [sessionId, nodeId, address, chain, onComplete, onUnavailable, onTimeout]);
}
