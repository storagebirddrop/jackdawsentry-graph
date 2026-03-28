import { useEffect, useRef, useState } from 'react';

import { getBridgeHopStatus } from '../api/client';
import type {
  BridgeHopData,
  BridgeHopStatusResponse,
  InvestigationNode,
} from '../types/graph';

const POLL_INTERVAL_MS = 30_000;

export interface BridgeStatusRefreshState {
  isPolling: boolean;
  lastCheckedAt: string | null;
  errorMessage: string | null;
}

interface UseBridgeHopPollerArgs {
  sessionId: string;
  node: InvestigationNode | null;
  onStatus: (nodeId: string, status: BridgeHopStatusResponse) => void;
  onNotice: (message: string, tone?: 'info' | 'error') => void;
}

export function useBridgeHopPoller({
  sessionId,
  node,
  onStatus,
  onNotice,
}: UseBridgeHopPollerArgs): BridgeStatusRefreshState {
  const [isPolling, setIsPolling] = useState(false);
  const [lastCheckedAt, setLastCheckedAt] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const terminalNoticeKeyRef = useRef<string | null>(null);
  const nodeId = node?.node_id ?? null;
  const hop =
    node?.node_type === 'bridge_hop'
      ? ((node.bridge_hop_data ?? node.node_data) as BridgeHopData | undefined)
      : null;
  const hopId = hop?.hop_id ?? null;
  const hopStatus = hop?.status ?? null;

  useEffect(() => {
    if (!nodeId || !hopId || hopStatus !== 'pending') {
      setIsPolling(false);
      setLastCheckedAt(null);
      setErrorMessage(null);
      return;
    }

    let cancelled = false;
    let timerId: ReturnType<typeof setTimeout> | null = null;
    const activeNodeId = nodeId;
    const activeHopId = hopId;

    setIsPolling(true);
    setErrorMessage(null);

    async function poll(): Promise<void> {
      try {
        const status = await getBridgeHopStatus(sessionId, activeHopId);
        if (cancelled) return;

        onStatus(activeNodeId, status);
        setLastCheckedAt(status.updated_at ?? new Date().toISOString());
        setErrorMessage(null);

        if (status.status !== 'pending') {
          setIsPolling(false);
          const noticeKey = `${status.hop_id}:${status.status}`;
          if (terminalNoticeKeyRef.current !== noticeKey) {
            terminalNoticeKeyRef.current = noticeKey;
            const terminalLabel =
              status.status === 'completed'
                ? 'resolved'
                : status.status === 'expired'
                  ? 'expired'
                  : status.status;
            onNotice(
              `Bridge hop ${terminalLabel}. The inspector and canvas now reflect the latest known status.`,
              status.status === 'completed' ? 'info' : 'error',
            );
          }
          return;
        }
      } catch {
        if (cancelled) return;
        setErrorMessage('Bridge status refresh failed. Retrying automatically.');
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
  }, [sessionId, nodeId, hopId, hopStatus, onStatus, onNotice]);

  return { isPolling, lastCheckedAt, errorMessage };
}
