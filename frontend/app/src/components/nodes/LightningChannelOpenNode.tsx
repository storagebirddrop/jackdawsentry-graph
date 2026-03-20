import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, LightningChannelOpenData } from '../../types/graph';
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
} from '../graphVisuals';

interface LightningChannelOpenNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

export default function LightningChannelOpenNode({ data }: NodeProps) {
  const d = data as unknown as LightningChannelOpenNodeData;
  const channel = (d.lightning_channel_open_data ?? d.node_data) as
    | LightningChannelOpenData
    | undefined;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeSemanticAccentColor(d, appearance, '#f2a900');

  if (!channel?.channel_id) {
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
        <div style={{ color: '#f87171' }}>Invalid Lightning channel data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  const peerSummary = [channel.local_alias || channel.local_pubkey, channel.remote_alias || channel.remote_pubkey]
    .filter(Boolean)
    .join(' <-> ');
  const capacityLabel = formatNative(channel.capacity_btc, 'BTC');

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
                Lightning
              </div>
              <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                Channel Open
              </div>
            </div>
            <span style={{ ...badgeStyle(channel.status === 'closed' ? '#64748b' : accent), textTransform: 'uppercase' }}>
              {channel.status ?? 'open'}
            </span>
          </div>

          {capacityLabel && (
            <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 600 }}>
              {capacityLabel}
            </div>
          )}

          {peerSummary && (
            <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
              {peerSummary}
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            <span style={badgeStyle(accent)}>Lightning</span>
            {channel.short_channel_id && (
              <span style={badgeStyle('#475569')}>{channel.short_channel_id}</span>
            )}
            {channel.is_private !== undefined && (
              <span style={badgeStyle(channel.is_private ? '#7c3aed' : '#2563eb')}>
                {channel.is_private ? 'Private' : 'Public'}
              </span>
            )}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
