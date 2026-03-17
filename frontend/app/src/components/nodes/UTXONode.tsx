/**
 * UTXONode — custom React Flow node for Bitcoin-family UTXO outputs.
 *
 * Displays: address (short), script type badge, value in BTC,
 * change-output indicator, CoinJoin halt badge.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, UTXONodeData } from '../../types/graph';

interface UTXONodeComponentData extends InvestigationNode {
  branch_color: string;
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

  return (
    <div
      style={{
        border: `2px solid ${utxo.is_coinjoin_halt ? '#b45309' : d.branch_color}`,
        borderRadius: 8,
        background: '#1c1917',
        color: '#f5f5f4',
        padding: '6px 10px',
        minWidth: 160,
        fontFamily: 'monospace',
        fontSize: 11,
      }}
    >
      <Handle type="target" position={Position.Left} />

      {/* Script type badge */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span
          style={{
            background: '#292524',
            borderRadius: 3,
            padding: '1px 5px',
            fontSize: 9,
            color: '#a8a29e',
            fontFamily: 'sans-serif',
          }}
        >
          {scriptLabel}
        </span>

        {/* Change / CoinJoin flags */}
        <div style={{ display: 'flex', gap: 3 }}>
          {utxo.is_probable_change && (
            <span style={badgeStyle('#1c1917', '#d6d3d1', '#57534e')}>CHANGE</span>
          )}
          {utxo.is_coinjoin_halt && (
            <span style={badgeStyle('#78350f', '#fef3c7')}>COINJOIN</span>
          )}
        </div>
      </div>

      {/* Address */}
      <div style={{ color: '#a8a29e', marginTop: 4, fontSize: 10 }}>
        {shortAddr(utxo.address)}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function shortAddr(addr: string): string {
  if (addr.length <= 14) return addr;
  return `${addr.slice(0, 7)}…${addr.slice(-5)}`;
}

function badgeStyle(bg: string, text: string, border?: string) {
  return {
    background: bg,
    color: text,
    border: border ? `1px solid ${border}` : undefined,
    borderRadius: 3,
    padding: '1px 4px',
    fontSize: 8,
    fontFamily: 'sans-serif',
    fontWeight: 700,
    letterSpacing: '0.5px',
  } as React.CSSProperties;
}
