import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { BtcSidechainPegData, InvestigationNode } from '../../types/graph';
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

interface PegNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

const SIDECHAIN_COLORS: Record<string, string> = {
  liquid: '#12b3a8',
  rootstock: '#f97316',
  stacks: '#5546ff',
};

export default function BtcSidechainPegNode({ data }: NodeProps) {
  const d = data as unknown as PegNodeData;
  const peg = (d.btc_sidechain_peg_data ?? d.node_data) as
    | BtcSidechainPegData
    | undefined;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;

  if (!peg?.sidechain) {
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
        <div style={{ color: '#f87171' }}>Invalid peg data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }

  const sidechain = peg.sidechain.toLowerCase();
  const accent = SIDECHAIN_COLORS[sidechain] ?? '#0f766e';
  const title = d.node_type === 'btc_sidechain_peg_in' ? 'Peg In' : 'Peg Out';
  const route = `${peg.asset_in} -> ${peg.asset_out}`;

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
                Bitcoin sidechain
              </div>
              <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 4 }}>
                {title}
              </div>
            </div>
            <span style={{ ...badgeStyle(accent), textTransform: 'uppercase' }}>
              {peg.sidechain}
            </span>
          </div>

          <div style={{ color: '#0f172a', marginTop: 10, fontSize: 13, fontWeight: 600 }}>
            {route}
          </div>

          {peg.amount_btc !== undefined && (
            <div style={{ color: '#475569', fontSize: 12, marginTop: 6 }}>
              {formatNative(peg.amount_btc, 'BTC')}
            </div>
          )}

          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {peg.mechanism && <span style={badgeStyle('#475569')}>{peg.mechanism}</span>}
            {peg.status && <span style={badgeStyle('#2563eb')}>{peg.status}</span>}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
