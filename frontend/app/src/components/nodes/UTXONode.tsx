/**
 * UTXONode — custom React Flow node for Bitcoin-family UTXO outputs.
 *
 * Displays: address (short), script type badge, value in BTC,
 * change-output indicator, CoinJoin halt badge.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, UTXONodeData } from '../../types/graph';
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

interface UTXONodeComponentData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

const SCRIPT_LABELS: Record<string, string> = {
  utxo_p2pkh: 'P2PKH',
  utxo_p2sh: 'P2SH',
  utxo_p2wpkh: 'P2WPKH',
  utxo_p2tr: 'P2TR',
  utxo_op_return: 'OP_RETURN',
};

export default function UTXONode({ data }: NodeProps) {
  const d = data as unknown as UTXONodeComponentData;
  const utxo = d.node_data as UTXONodeData;
  const scriptLabel = SCRIPT_LABELS[utxo.address_type ?? ''] ?? utxo.script_type ?? '?';
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeAccentColor(
    d,
    appearance,
    utxo.is_coinjoin_halt ? '#b45309' : d.branch_color,
  );

  return (
    <div
      style={{
        border: `1px solid ${accent}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 220,
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
        fontSize: 11,
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
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
            <span style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              UTXO
            </span>
            <span style={badgeStyle('#475569')}>{scriptLabel}</span>
          </div>
          <div style={{ color: '#0f172a', fontWeight: 700, fontSize: 14, marginTop: 6 }}>
            {shortAddr(utxo.address || '')}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {utxo.is_probable_change && (
              <span style={badgeStyle('#64748b')}>Change</span>
            )}
            {utxo.is_coinjoin_halt && (
              <span style={badgeStyle('#b45309')}>CoinJoin</span>
            )}
          </div>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function shortAddr(addr: string): string {
  if (addr.length <= 14) return addr;
  return `${addr.slice(0, 7)}…${addr.slice(-5)}`;
}
