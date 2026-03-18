/**
 * ClusterSummaryNode — collapsed subtree placeholder shown when a subtree
 * exceeds the display threshold.  Clicking "Expand" re-issues the paginated
 * expansion call.
 */

import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { InvestigationNode, ClusterSummaryData } from '../../types/graph';

interface ClusterNodeData extends InvestigationNode {
  branch_color: string;
  onExpand?: () => void;
}

type ClusterSummaryNodeType = Node<ClusterNodeData>;

export default function ClusterSummaryNode({ data }: NodeProps<ClusterSummaryNodeType>) {
  if (!data || !data.node_data) {
    return (
      <div style={{ border: '2px dashed #475569', borderRadius: 8, background: '#1e293b', color: '#64748b', padding: '6px 10px', minWidth: 140, fontSize: 11, textAlign: 'center' }}>
        <Handle type="target" position={Position.Left} />
        <div>No cluster data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }
  const cluster = data.node_data as ClusterSummaryData;

  return (
    <div
      style={{
        border: `2px dashed ${data.branch_color}`,
        borderRadius: 8,
        background: '#1e293b',
        color: '#f1f5f9',
        padding: '6px 10px',
        minWidth: 140,
        fontSize: 11,
        textAlign: 'center',
      }}
    >
      <Handle type="target" position={Position.Left} />

      <div style={{ color: '#94a3b8', fontSize: 12, fontWeight: 700 }}>
        {cluster.total_nodes} nodes
      </div>

      <div style={{ color: '#64748b', fontSize: 9, marginTop: 2 }}>
        {cluster.dominant_type}
        {cluster.max_risk_score !== undefined &&
          ` · risk ${(cluster.max_risk_score * 100).toFixed(0)}%`}
      </div>

      {data.onExpand && (
        <button
          onClick={data.onExpand}
          style={{
            marginTop: 6,
            padding: '2px 8px',
            background: data.branch_color,
            border: 'none',
            borderRadius: 4,
            color: '#fff',
            fontSize: 10,
            cursor: 'pointer',
          }}
        >
          Expand
        </button>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
