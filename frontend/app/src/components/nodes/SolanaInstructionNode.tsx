/**
 * SolanaInstructionNode — displays a decoded Solana instruction.
 *
 * Shows program name (or short program ID), instruction type,
 * decode status badge (full / partial / unknown).
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, SolanaInstructionData } from '../../types/graph';

interface SolanaNodeData extends InvestigationNode {
  branch_color: string;
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

export default function SolanaInstructionNode({ data }: NodeProps<SolanaNodeData>) {
  const ix = data.node_data as SolanaInstructionData;
  const decodeStatus = ix.decode_status ?? 'unknown';
  const decodeColor = DECODE_COLORS[decodeStatus] ?? DECODE_COLORS.unknown;

  return (
    <div
      style={{
        border: `2px solid #9945ff`,
        borderRadius: 8,
        background: '#0d0e1a',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 160,
        fontSize: 11,
      }}
    >
      <Handle type="target" position={Position.Left} />

      {/* Program name + decode status */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ color: '#9945ff', fontWeight: 700, fontSize: 12 }}>
          {displayProgram(ix)}
        </span>
        <span
          style={{
            background: decodeColor,
            color: '#fff',
            borderRadius: 3,
            padding: '1px 4px',
            fontSize: 8,
            fontFamily: 'sans-serif',
            fontWeight: 700,
          }}
          title={`Decode: ${decodeStatus}`}
        >
          {decodeStatus.toUpperCase()}
        </span>
      </div>

      {/* Instruction type */}
      {ix.instruction_type && (
        <div style={{ color: '#94a3b8', marginTop: 3, fontSize: 10 }}>
          {ix.instruction_type}
        </div>
      )}

      {/* Decoded args summary */}
      {ix.decoded_args && (
        <div style={{ color: '#64748b', marginTop: 2, fontSize: 9 }}>
          {Object.keys(ix.decoded_args).slice(0, 3).join(' · ')}
          {Object.keys(ix.decoded_args).length > 3 && ' …'}
        </div>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
