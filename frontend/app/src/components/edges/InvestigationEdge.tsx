import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react';

import type { InvestigationEdge } from '../../types/graph';
import {
  formatNative,
  formatTimestamp,
  formatUsd,
} from '../graphVisuals';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';

interface InvestigationEdgeComponentData extends InvestigationEdge {
  branch_color?: string;
  appearance?: GraphAppearanceState;
}

export default function InvestigationEdgeComponent(props: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath(props);
  const data = (props.data ?? {}) as unknown as InvestigationEdgeComponentData;
  const appearance = data.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const accent = data.branch_color ?? '#3b82f6';
  const isBridgeEdge = data.edge_type === 'bridge_source' || data.edge_type === 'bridge_dest';
  const bridgeLabel = isBridgeEdge
    ? data.edge_type === 'bridge_source'
      ? 'Bridge ingress'
      : 'Bridge egress'
    : null;

  const valueLabel = appearance.showValues
    ? appearance.amountsInFiat
      ? formatUsd(data.value_fiat ?? data.fiat_value_usd)
      : formatNative(data.value_native, data.asset_symbol)
    : null;
  const dateLabel = appearance.showTxDate
    ? formatTimestamp(data.timestamp, appearance.showTxTime)
    : null;
  const changeLabel = data.is_suspected_change ? 'Change output' : null;
  const labels = [bridgeLabel, valueLabel, dateLabel, changeLabel].filter(
    (value): value is string => Boolean(value),
  );

  return (
    <>
      <BaseEdge
        path={edgePath}
        markerEnd={props.markerEnd}
        style={{
          ...(props.style ?? {}),
          stroke: accent,
          strokeWidth: props.selected ? 3.2 : isBridgeEdge ? 3 : 2.1,
          strokeDasharray: isBridgeEdge ? '7 5' : undefined,
          opacity: props.selected ? 1 : 0.94,
        }}
      />
      {labels.length > 0 && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '4px 7px',
              borderRadius: 10,
              background: 'rgba(10, 18, 32, 0.92)',
              border: `1px solid ${props.selected ? accent : `${accent}35`}`,
              boxShadow: '0 10px 24px rgba(2, 6, 23, 0.16)',
              color: '#e2e8f0',
              fontSize: 11,
              fontWeight: 600,
              whiteSpace: 'nowrap',
              backdropFilter: 'blur(10px)',
            }}
            className="nodrag nopan"
          >
            {labels.map((label) => (
              <span
                key={label}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '2px 6px',
                  borderRadius: 999,
                  background: 'rgba(30, 41, 59, 0.88)',
                }}
              >
                {label}
              </span>
            ))}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
