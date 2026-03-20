/**
 * BridgeHopDrawer — side panel showing full BridgeHopData detail on click.
 *
 * Opens when a BridgeHopNode is selected. Polls the status endpoint every
 * 30 seconds while status is 'pending'.
 */

import { useEffect, useRef } from 'react';
import { getBridgeHopStatus } from '../api/client';
import type { BridgeHopData, BridgeHopStatusResponse } from '../types/graph';

interface Props {
  sessionId: string;
  nodeId: string;
  hopData: BridgeHopData;
  onClose: () => void;
  onRefreshHop?: () => void;
}

const POLL_INTERVAL_MS = 30_000;

export default function BridgeHopDrawer({ sessionId, nodeId, hopData, onClose, onRefreshHop }: Props) {
  const hopId = hopData.hop_id;
  const bridgeHop = hopData as BridgeHopData & {
    dest_chain?: string;
    dest_asset?: string;
    correlation_conf?: number;
  };
  const destinationChain = bridgeHop.destination_chain ?? bridgeHop.dest_chain ?? '—';
  const destinationAsset = bridgeHop.destination_asset ?? bridgeHop.dest_asset ?? '—';
  const confidence = bridgeHop.correlation_confidence ?? bridgeHop.correlation_conf;

  // Poll while pending
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (hopData.status !== 'pending') return;

    async function poll() {
      try {
        const status: BridgeHopStatusResponse = await getBridgeHopStatus(sessionId, hopId);
        if (status.status !== 'pending') {
          // Status changed — trigger a re-expand so the graph updates
          console.log(`Bridge hop ${hopId} resolved: ${status.status}`);
          if (intervalRef.current) clearInterval(intervalRef.current);
          // Notify parent so it can refresh the graph for the updated hop.
          onRefreshHop?.();
        }
      } catch {
        // Swallow — backend may not have it yet
      }
    }

    // Poll immediately on mount
    poll();
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [hopId, hopData.status, sessionId, onRefreshHop]);

  const statusColor =
    hopData.status === 'completed' ? '#10b981'
    : hopData.status === 'failed' ? '#ef4444'
    : '#f59e0b';

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        width: 320,
        height: '100%',
        background: '#1e293b',
        borderLeft: '1px solid #334155',
        zIndex: 200,
        overflowY: 'auto',
        fontFamily: 'sans-serif',
        fontSize: 12,
        color: '#f1f5f9',
        padding: 20,
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <span style={{ color: '#a78bfa', fontWeight: 700, fontSize: 15 }}>
          Bridge Hop
        </span>
        <button
          onClick={onClose}
          aria-label="Close"
          style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 16 }}
        >
          ✕
        </button>
      </div>

      {/* Status */}
      <Row label="Status">
        <span style={{ color: statusColor, fontWeight: 700 }}>
          {hopData.status.toUpperCase()}
          {hopData.status === 'pending' && ' (polling every 30s)'}
        </span>
      </Row>

      <Row label="Protocol">{hopData.protocol_id}</Row>
      <Row label="Mechanism">{hopData.mechanism}</Row>
      <Row label="Source chain">{hopData.source_chain}</Row>
      <Row label="Destination chain">{destinationChain}</Row>
      <Row label="Source asset">{bridgeHop.source_asset ?? '—'}</Row>
      <Row label="Destination asset">{destinationAsset}</Row>
      <Row label="Confidence">
        {Number.isFinite(confidence)
          ? (confidence * 100).toFixed(0) + '%'
          : '—'
        }
      </Row>
      {bridgeHop.time_delta_seconds !== undefined && (
        <Row label="Time delta">
          {bridgeHop.time_delta_seconds}s
        </Row>
      )}
      <Row label="Hop ID">
        <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{bridgeHop.hop_id}</span>
      </Row>
      <Row label="Node ID">
        <span style={{ fontFamily: 'monospace', fontSize: 10 }}>{nodeId}</span>
      </Row>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '6px 0',
        borderBottom: '1px solid #334155',
        gap: 8,
      }}
    >
      <span style={{ color: '#64748b', flexShrink: 0 }}>{label}</span>
      <span style={{ textAlign: 'right', wordBreak: 'break-all' }}>{children}</span>
    </div>
  );
}
