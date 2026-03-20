/**
 * SolanaInstructionNode — displays a decoded Solana instruction.
 *
 * Shows program name (or short program ID), instruction type,
 * decode status badge (full / partial / unknown).
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, SolanaInstructionData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeSemanticAccentColor,
  nodeGlyphKind,
  solanaProgramLabel,
} from '../graphVisuals';

interface SolanaNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
}

const DECODE_COLORS: Record<string, string> = {
  full: '#10b981',
  partial: '#f59e0b',
  unknown: '#ef4444',
};

export default function SolanaInstructionNode({ data: rawData }: NodeProps) {
  const data = rawData as unknown as SolanaNodeData;
  const ix = data.node_data as SolanaInstructionData;
  const decodeStatus = ix.decode_status ?? 'unknown';
  const decodeColor = DECODE_COLORS[decodeStatus] ?? DECODE_COLORS.unknown;
  const appearance = data.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeSemanticAccentColor(data, appearance, '#9945ff');
  const programLabel = solanaProgramLabel(ix.program_id, ix.program_name);

  return (
    <div
      style={{
        border: `1px solid ${accent}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 220,
        fontSize: 11,
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(data)} accent={accent} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
            <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Solana instruction
            </div>
            <span style={badgeStyle(decodeColor)} title={`Decode: ${decodeStatus}`}>
              {decodeStatus.toUpperCase()}
            </span>
          </div>
          <div style={{ color: accent, fontWeight: 700, fontSize: 15, marginTop: 6 }}>
            {programLabel}
          </div>

          {ix.instruction_type && (
            <div style={{ color: '#334155', marginTop: 8, fontSize: 12 }}>
              {ix.instruction_type}
            </div>
          )}

          {ix.decoded_args && (
            <div style={{ color: '#64748b', marginTop: 6, fontSize: 11 }}>
              {Object.keys(ix.decoded_args).slice(0, 3).join(' · ')}
              {Object.keys(ix.decoded_args).length > 3 && ' ...'}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            <span style={badgeStyle(accent)}>{programLabel}</span>
          </div>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
