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
  nodeAccentColor,
  nodeGlyphKind,
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

const PROGRAM_DISPLAY: Record<string, string> = {
  'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA': 'SPL Token',
  'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4': 'Jupiter v6',
  '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'Raydium AMM',
  whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3sFKDW6: 'Orca Whirlpool',
  worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth: 'Wormhole',
  MayanU2yS5r3fUBoPRKmHtCm9e4mNR7TXbmvZs2KN3k: 'Mayan',
};

function displayProgram(data: SolanaInstructionData): string {
  if (data.program_name) return data.program_name;
  const known = PROGRAM_DISPLAY[data.program_id];
  if (known) return known;
  return `${data.program_id.slice(0, 6)}…${data.program_id.slice(-4)}`;
}

export default function SolanaInstructionNode({ data: rawData }: NodeProps) {
  const data = rawData as unknown as SolanaNodeData;
  const ix = data.node_data as SolanaInstructionData;
  const decodeStatus = ix.decode_status ?? 'unknown';
  const decodeColor = DECODE_COLORS[decodeStatus] ?? DECODE_COLORS.unknown;
  const appearance = data.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeAccentColor(data, appearance, '#9945ff');

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
            {displayProgram(ix)}
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
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
