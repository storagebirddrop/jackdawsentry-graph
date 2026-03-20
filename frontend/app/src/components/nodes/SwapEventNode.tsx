/**
 * SwapEventNode — displays an on-chain swap event (DEX/AMM).
 *
 * Shows protocol, input→output asset pair, exchange rate.
 * Asset transformation is a first-class graph event — not just an edge property.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, SwapEventData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  formatNative,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeSemanticAccentColor,
  nodeGlyphKind,
  swapProtocolLabel,
} from '../graphVisuals';

interface SwapNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

export default function SwapEventNode({ data }: NodeProps) {
  // NodeProps<T> in @xyflow/react v12 types data as Record<string,unknown>;
  // cast via unknown to reach our richer interface.
  const swapData = data as unknown as SwapNodeData;
  const swap = swapData.node_data as SwapEventData;
  const appearance = swapData.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeSemanticAccentColor(swapData, appearance, '#0f766e');
  const protocolLabel = swapProtocolLabel(swap.protocol_id);

  return (
    <div
      style={{
        border: `1px solid ${accent}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 230,
        fontSize: 11,
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(swapData)} accent={accent} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Swap
          </div>
          <div style={{ color: accent, fontWeight: 700, fontSize: 14, marginTop: 4 }}>
            {protocolLabel}
          </div>
          <div style={{ color: '#64748b', fontSize: 11, marginTop: 4 }}>
            {swap.chain?.toUpperCase() ?? 'multi-chain'} liquidity route
          </div>

          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ ...badgeStyle(accent), textTransform: 'uppercase' }}>{swap.input_asset ?? '--'}</span>
            <span style={{ color: '#64748b', fontWeight: 700 }}>to</span>
            <span style={{ ...badgeStyle('#2563eb'), textTransform: 'uppercase' }}>{swap.output_asset ?? '--'}</span>
          </div>

          {(swap.input_amount !== undefined || swap.output_amount !== undefined) && (
            <div style={{ color: '#334155', fontSize: 12, marginTop: 10 }}>
              {[
                formatNative(swap.input_amount, swap.input_asset) ?? '--',
                formatNative(swap.output_amount, swap.output_asset) ?? '--',
              ].join(' -> ')}
            </div>
          )}

          {swap.exchange_rate !== undefined && (
            <div style={{ color: '#64748b', fontSize: 11, marginTop: 5 }}>
              rate {swap.exchange_rate.toFixed(6)}
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            <span style={badgeStyle(accent)}>{protocolLabel}</span>
            {swap.chain && <span style={badgeStyle('#334155')}>{swap.chain}</span>}
          </div>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
