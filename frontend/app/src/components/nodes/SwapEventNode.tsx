/**
 * SwapEventNode — displays an on-chain swap event (DEX/AMM).
 *
 * Shows protocol, input→output asset pair, exchange rate.
 * Asset transformation is a first-class graph event — not just an edge property.
 */

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, SwapEventData } from '../../types/graph';

interface SwapNodeData extends InvestigationNode {
  branch_color: string;
}

type SwapEventNodeType = Node<SwapNodeData>;

export default function SwapEventNode({ data }: NodeProps<SwapEventNodeType>) {
  const swap = data.node_data as SwapEventData;

  return (
    <div
      style={{
        border: `2px solid #0891b2`,
        borderRadius: 8,
        background: '#0c1a1f',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 170,
        fontSize: 11,
      }}
    >
      <Handle type="target" position={Position.Left} />

      {/* Protocol */}
      <div style={{ color: '#22d3ee', fontWeight: 700, fontSize: 12 }}>
        {(swap.protocol_id ?? 'UNKNOWN').toUpperCase()}
      </div>

      {/* Asset pair */}
      <div style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ color: '#f8fafc', fontWeight: 600 }}>{swap.input_asset ?? '—'}</span>
        <span style={{ color: '#64748b' }}>→</span>
        <span style={{ color: '#f8fafc', fontWeight: 600 }}>{swap.output_asset ?? '—'}</span>
      </div>

      {/* Exchange rate */}
      {swap.exchange_rate !== undefined && (
        <div style={{ color: '#64748b', fontSize: 9, marginTop: 2 }}>
          rate {swap.exchange_rate.toFixed(6)}
        </div>
      )}

      {/* Amounts */}
      {(swap.input_amount !== undefined || swap.output_amount !== undefined) && (
        <div style={{ color: '#94a3b8', fontSize: 9, marginTop: 1 }}>
          {swap.input_amount?.toFixed(4)} → {swap.output_amount?.toFixed(4)}
        </div>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
