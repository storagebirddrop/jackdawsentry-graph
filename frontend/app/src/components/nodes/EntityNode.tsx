/**
 * EntityNode — displays an attributed entity (exchange, mixer, VASP, etc.)
 * with name, category, address count, and risk score.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, EntityNodeData } from '../../types/graph';

interface EntityNodeComponentData extends InvestigationNode {
  branch_color: string;
}

const CATEGORY_COLORS: Record<string, string> = {
  exchange: '#2563eb',
  mixer: '#7c3aed',
  defi: '#0891b2',
  darknet: '#991b1b',
  gambling: '#b45309',
  sanctioned: '#dc2626',
  unknown: '#475569',
};

export default function EntityNode({ data }: NodeProps) {
  const d = data as unknown as EntityNodeComponentData;
  const entity = d.node_data as EntityNodeData;
  const catColor = CATEGORY_COLORS[entity.category] ?? CATEGORY_COLORS.unknown;

  return (
    <div
      style={{
        border: `2px solid ${catColor}`,
        borderRadius: 8,
        background: '#0f172a',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 160,
        fontSize: 11,
      }}
    >
      <Handle type="target" position={Position.Left} />

      <div style={{ fontWeight: 700, color: '#f8fafc', fontSize: 13 }}>
        {entity.name}
      </div>

      <div
        style={{
          display: 'inline-block',
          background: catColor,
          borderRadius: 3,
          padding: '1px 5px',
          fontSize: 9,
          fontWeight: 700,
          color: '#fff',
          marginTop: 3,
          letterSpacing: '0.5px',
          textTransform: 'uppercase',
        }}
      >
        {entity.category}
      </div>

      <div style={{ color: '#94a3b8', marginTop: 3, fontSize: 10 }}>
        {entity.address_count} addresses
        {entity.jurisdiction && ` · ${entity.jurisdiction}`}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
