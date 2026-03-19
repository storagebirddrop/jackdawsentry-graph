/**
 * ClusterSummaryNode — collapsed subtree placeholder shown when a subtree
 * exceeds the display threshold.  Clicking "Expand" re-issues the paginated
 * expansion call.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, ClusterSummaryData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  GraphGlyph,
  glyphSurfaceStyle,
  nodeAccentColor,
  nodeGlyphKind,
} from '../graphVisuals';

interface ClusterNodeData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
  onExpand?: () => void;
}

export default function ClusterSummaryNode({ data }: NodeProps) {
  // NodeProps<T> in @xyflow/react v12 types data as Record<string,unknown>;
  // cast via unknown to reach our richer interface.
  const clusterData = data as unknown as ClusterNodeData;
  if (!clusterData || !clusterData.node_data) {
    return (
      <div style={{ border: '2px dashed #475569', borderRadius: 8, background: '#1e293b', color: '#64748b', padding: '6px 10px', minWidth: 140, fontSize: 11, textAlign: 'center' }}>
        <Handle type="target" position={Position.Left} />
        <div>No cluster data</div>
        <Handle type="source" position={Position.Right} />
      </div>
    );
  }
  const cluster = clusterData.node_data as ClusterSummaryData;
  const appearance = clusterData.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = nodeAccentColor(clusterData, appearance, clusterData.branch_color);

  return (
    <div
      style={{
        border: `1px dashed ${accent}`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.96)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 200,
        fontSize: 11,
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(clusterData)} accent={accent} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Cluster summary
          </div>
          <div style={{ color: '#0f172a', fontSize: 16, fontWeight: 700, marginTop: 4 }}>
            {cluster.total_nodes} nodes
          </div>
          <div style={{ color: '#64748b', fontSize: 11, marginTop: 6 }}>
            {cluster.dominant_type}
            {cluster.max_risk_score !== undefined &&
              ` · risk ${(cluster.max_risk_score * 100).toFixed(0)}%`}
          </div>
        </div>
      </div>

      {clusterData.onExpand && (
        <button
          onClick={(event) => {
            event.stopPropagation();
            clusterData.onExpand?.();
          }}
          style={{
            marginTop: 12,
            padding: '8px 12px',
            background: accent,
            border: 'none',
            borderRadius: 10,
            color: '#fff',
            fontSize: 11,
            fontWeight: 700,
            cursor: 'pointer',
          }}
        >
          Expand cluster
        </button>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
