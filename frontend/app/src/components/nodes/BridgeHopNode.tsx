/**
 * BridgeHopNode — displays a cross-chain bridge hop with source→dest chain,
 * protocol name, assets, confidence, and status indicator.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, BridgeHopData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeAccentColor,
  nodeGlyphKind,
} from '../graphVisuals';

interface BridgeNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

function statusColor(status: string): string {
  if (status === 'completed') return '#10b981';
  if (status === 'failed') return '#ef4444';
  return '#f59e0b'; // pending
}

export default function BridgeHopNode({ data }: NodeProps) {
  const d = data as unknown as BridgeNodeData;
  const hop = d.node_data as BridgeHopData & {
    dest_chain?: string;
    dest_asset?: string;
    correlation_conf?: number;
  };
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const activity = d.activity_summary;
  const destinationChain = hop.destination_chain ?? hop.dest_chain ?? '?';
  const destinationAsset = hop.destination_asset ?? hop.dest_asset ?? '?';
  const confidence = hop.correlation_confidence ?? hop.correlation_conf;

  // Defensive guards for required properties
  if (!hop?.hop_id) {
    return (
      <div style={{ border: '2px solid #ef4444', borderRadius: 8, background: '#1e1b4b', color: '#f1f5f9', padding: '6px 10px', minWidth: 180, fontSize: 11 }}>
        <Handle type="target" position={Position.Left} />
        <div style={{ color: '#f87171' }}>Invalid Bridge Hop Data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  const accent = nodeAccentColor(d, appearance, '#7c3aed');

  return (
    <div
      style={{
        border: `1px solid ${accent}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 240,
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
                Bridge hop
              </div>
              <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                {(hop.protocol_id || 'UNKNOWN').toUpperCase()}
              </div>
            </div>
            <span
              style={{
                ...badgeStyle(statusColor(hop.status || 'pending')),
                textTransform: 'uppercase',
              }}
            >
              {hop.status || 'pending'}
            </span>
          </div>

          <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 600 }}>
            {[hop.source_chain ?? '?', destinationChain].join(' -> ')}
          </div>

          {(hop.source_asset || hop.destination_asset) && (
            <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
              {[hop.source_asset ?? '?', destinationAsset].join(' -> ')}
            </div>
          )}

          <div style={{ color: '#64748b', fontSize: 11, marginTop: 8 }}>
            confidence {Number.isFinite(confidence) ? `${(confidence * 100).toFixed(0)}%` : '--'}
          </div>
          {activity?.source_tx_hash && (
            <div style={{ color: '#64748b', fontSize: 11, marginTop: 6 }}>
              tx {activity.source_tx_hash.slice(0, 10)}…
              {activity.destination_tx_hash && ` -> ${activity.destination_tx_hash.slice(0, 10)}…`}
            </div>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
