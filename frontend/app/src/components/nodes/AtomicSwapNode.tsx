import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { AtomicSwapData, InvestigationNode } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  formatNative,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeGlyphKind,
} from '../graphVisuals';

interface AtomicSwapNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

export default function AtomicSwapNode({ data }: NodeProps) {
  const d = data as unknown as AtomicSwapNodeData;
  const swap = (d.atomic_swap_data ?? d.node_data) as AtomicSwapData | undefined;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = '#2563eb';

  if (!swap?.swap_id) {
    return (
      <div
        style={{
          border: '2px solid #ef4444',
          borderRadius: 8,
          background: '#1e1b4b',
          color: '#f1f5f9',
          padding: '6px 10px',
          minWidth: 180,
          fontSize: 11,
        }}
      >
        <Handle type="target" position={Position.Left} />
        <div style={{ color: '#f87171' }}>Invalid atomic swap</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  return (
    <div
      style={{
        border: `1px solid ${accent}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 250,
        fontSize: 11,
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(d)} accent={accent} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <div>
              <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Cross-chain HTLC
              </div>
              <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                Atomic Swap
              </div>
            </div>
            <span style={{ ...badgeStyle('#7c3aed'), textTransform: 'uppercase' }}>
              {swap.state ?? 'partial'}
            </span>
          </div>

          <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 600 }}>
            {swap.source_asset} {'->'} {swap.destination_asset}
          </div>

          <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
            {swap.source_chain} {'->'} {swap.destination_chain}
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            {swap.source_amount !== undefined && (
              <span style={badgeStyle('#1d4ed8')}>
                {formatNative(swap.source_amount, swap.source_asset) ?? swap.source_asset}
              </span>
            )}
            {swap.destination_amount !== undefined && (
              <span style={badgeStyle('#0f766e')}>
                {formatNative(swap.destination_amount, swap.destination_asset) ?? swap.destination_asset}
              </span>
            )}
          </div>

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {swap.protocol_id && <span style={badgeStyle('#475569')}>{swap.protocol_id}</span>}
            {swap.hashlock && <span style={badgeStyle(accent)}>hashlock</span>}
            {swap.timelock !== undefined && <span style={badgeStyle('#b45309')}>t+{swap.timelock}</span>}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
