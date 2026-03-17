/**
 * BridgeHopNode — displays a cross-chain bridge hop with source→dest chain,
 * protocol name, assets, confidence, and status indicator.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, BridgeHopData } from '../../types/graph';

interface BridgeNodeData extends InvestigationNode {
  branch_color: string;
}

function statusColor(status: string): string {
  if (status === 'completed') return '#10b981';
  if (status === 'failed') return '#ef4444';
  return '#f59e0b'; // pending
}

export default function BridgeHopNode({ data }: NodeProps) {
  const d = data as unknown as BridgeNodeData;
  const hop = d.node_data as BridgeHopData;

  return (
    <div
      style={{
        border: `2px solid #8b5cf6`,
        borderRadius: 8,
        background: '#1e1b4b',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 180,
        fontSize: 11,
      }}
    >
      <Handle type="target" position={Position.Left} />

      {/* Protocol + status */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: '#a78bfa', fontWeight: 700, fontSize: 12 }}>
          {hop.protocol_id.toUpperCase()}
        </span>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: statusColor(hop.status),
            display: 'inline-block',
          }}
          title={`Status: ${hop.status}`}
        />
      </div>

      {/* Chain route */}
      <div style={{ color: '#94a3b8', marginTop: 3 }}>
        {hop.source_chain} → {hop.destination_chain ?? '?'}
      </div>

      {/* Assets */}
      {(hop.source_asset || hop.destination_asset) && (
        <div style={{ color: '#60a5fa', fontSize: 10, marginTop: 2 }}>
          {hop.source_asset ?? '?'} → {hop.destination_asset ?? '?'}
        </div>
      )}

      {/* Confidence */}
      <div style={{ color: '#64748b', fontSize: 9, marginTop: 3 }}>
        confidence {(hop.correlation_confidence * 100).toFixed(0)}%
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
