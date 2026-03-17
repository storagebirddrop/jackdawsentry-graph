/**
 * AddressNode — custom React Flow node for blockchain addresses.
 *
 * Displays: short address, entity name (if attributed), risk badge,
 * sanction/mixer/CoinJoin badges, expand buttons.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, AddressNodeData } from '../../types/graph';

interface AddressNodeComponentData extends InvestigationNode {
  branch_color: string;
}

function riskColor(score?: number): string {
  if (score === undefined) return '#64748b';
  if (score >= 0.7) return '#ef4444';
  if (score >= 0.4) return '#f59e0b';
  return '#10b981';
}

function riskLabel(score?: number): string {
  if (score === undefined) return 'unknown';
  if (score >= 0.7) return 'high';
  if (score >= 0.4) return 'med';
  return 'low';
}

function shortAddr(addr: string): string {
  if (addr.length <= 12) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export default function AddressNode({ data }: NodeProps) {
  const d = data as unknown as AddressNodeComponentData;
  const addr = (d.address_data ?? d.node_data) as AddressNodeData;

  // Defensive guards for required properties
  if (!addr?.address) {
    return (
      <div style={{ border: '2px solid #ef4444', borderRadius: 8, background: '#1e293b', color: '#f1f5f9', padding: '6px 10px', minWidth: 160, fontFamily: 'monospace', fontSize: 11, position: 'relative' }}>
        <Handle type="source" position={Position.Right} />
        <Handle type="target" position={Position.Left} />
        <div style={{ color: '#f87171' }}>Invalid Address Data</div>
      </div>
    );
  }

  const safeBranchColor = d?.branch_color ?? '#475569';

  return (
    <div
      style={{
        border: `2px solid ${safeBranchColor}`,
        borderRadius: 8,
        background: '#1e293b',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 160,
        fontFamily: 'monospace',
        fontSize: 11,
        position: 'relative',
      }}
    >
      <Handle type="target" position={Position.Left} />

      {/* Risk dot + label */}
      <div
        style={{
          position: 'absolute',
          top: 6,
          right: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 3,
        }}
        aria-label={`Risk: ${addr.risk_score?.toFixed(2) ?? 'unknown'} (${riskLabel(addr.risk_score)})`}
        title={`Risk: ${addr.risk_score?.toFixed(2) ?? 'unknown'}`}
      >
        <span style={{ fontSize: 8, color: riskColor(addr.risk_score), fontFamily: 'sans-serif' }}>
          {riskLabel(addr.risk_score)}
        </span>
        <div
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: riskColor(addr.risk_score),
          }}
        />
      </div>

      {/* Address */}
      <div style={{ fontWeight: 600, color: '#94a3b8' }}>
        {shortAddr(addr.address)}
      </div>

      {/* Chain badge */}
      <div style={{ fontSize: 9, color: '#64748b', marginTop: 1 }}>
        {addr.chain}
      </div>

      {/* Entity name */}
      {addr.entity_name && (
        <div style={{ color: '#60a5fa', fontSize: 10, marginTop: 3 }}>
          {addr.entity_name}
        </div>
      )}

      {/* Flags */}
      <div style={{ display: 'flex', gap: 3, marginTop: 3, flexWrap: 'wrap' }}>
        {addr.is_sanctioned && (
          <span style={badgeStyle('#991b1b', '#fee2e2')}>SANCTIONED</span>
        )}
        {addr.is_mixer && (
          <span style={badgeStyle('#4c1d95', '#ede9fe')}>MIXER</span>
        )}
        {addr.is_coinjoin_halt && (
          <span style={badgeStyle('#78350f', '#fef3c7')}>COINJOIN</span>
        )}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function badgeStyle(bg: string, text: string) {
  return {
    background: bg,
    color: text,
    borderRadius: 3,
    padding: '1px 4px',
    fontSize: 8,
    fontFamily: 'sans-serif',
    fontWeight: 700,
    letterSpacing: '0.5px',
  } as React.CSSProperties;
}
