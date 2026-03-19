import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, LightningChannelCloseData } from '../../types/graph';
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

interface LightningChannelCloseNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

export default function LightningChannelCloseNode({ data }: NodeProps) {
  const d = data as unknown as LightningChannelCloseNodeData;
  const channel = (d.lightning_channel_close_data ?? d.node_data) as
    | LightningChannelCloseData
    | undefined;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = '#f2a900';

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
        <div style={{ color: '#f87171' }}>Invalid channel close</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  const peerSummary = [channel.local_alias ?? channel.local_pubkey, channel.remote_alias ?? channel.remote_pubkey]
    .filter(Boolean)
    .join(' <-> ');

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
                Lightning
              </div>
              <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                Channel Close
              </div>
            </div>
            <span style={{ ...badgeStyle('#475569'), textTransform: 'uppercase' }}>
              {channel.close_type ?? 'unknown'}
            </span>
          </div>

          <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 600 }}>
            {peerSummary || channel.channel_id}
          </div>

          {channel.settled_btc !== undefined && (
            <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
              {formatNative(channel.settled_btc, 'BTC')}
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {channel.status && <span style={badgeStyle('#2563eb')}>{channel.status}</span>}
            {channel.channel_id && <span style={badgeStyle(accent)}>{channel.channel_id}</span>}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
