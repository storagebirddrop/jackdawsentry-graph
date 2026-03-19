import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, EntityNodeData } from '../../types/graph';
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
  riskColor,
  riskLabel,
  semanticBadges,
} from '../graphVisuals';

interface EntityNodeComponentData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
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
  const entity = d.node_data as EntityNodeData & {
    protocol_id?: string;
    service_type?: string;
    display_name?: string;
    known_contracts?: string[];
  };
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;
  const activity = d.activity_summary;
  const isServiceInteraction = d.node_type === 'service' && activity != null;

  // Defensive guards for required properties
  if (!isServiceInteraction && !entity?.entity_id) {
    return (
      <div style={{ border: '2px solid #ef4444', borderRadius: 8, background: '#0f172a', color: '#f1f5f9', padding: '6px 10px', minWidth: 160, fontSize: 11 }}>
        <div style={{ color: '#f87171' }}>Invalid Entity Data</div>
      </div>
    );
  }

  const catColor = nodeAccentColor(
    d,
    appearance,
    CATEGORY_COLORS[entity.category] ?? CATEGORY_COLORS.unknown,
  );
  const badges = semanticBadges(d);

  return (
    <div
      style={{
        border: `1px solid ${catColor}55`,
        borderRadius: 18,
        background: 'rgba(255,255,255,0.97)',
        color: '#0f172a',
        padding: '14px 16px',
        minWidth: 240,
        maxWidth: 280,
        fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
        boxShadow: '0 14px 28px rgba(15, 23, 42, 0.08)',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(catColor)}>
            <GraphGlyph kind={nodeGlyphKind(d)} accent={catColor} />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <div style={{ color: '#64748b', fontSize: 10, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            {isServiceInteraction ? 'Activity' : 'Entity'}
          </div>
          <div style={{ fontWeight: 700, color: '#0f172a', fontSize: 16, lineHeight: 1.15, marginTop: 4 }}>
            {isServiceInteraction
              ? activity.title
              : entity.name}
          </div>
          <div style={{ color: '#64748b', marginTop: 5, fontSize: 12 }}>
            {isServiceInteraction
              ? [
                  activity.protocol_id?.replace(/_/g, ' '),
                  activity.tx_hash ? `${activity.tx_hash.slice(0, 10)}…` : undefined,
                ].filter(Boolean).join(' · ')
              : (
                  <>
                    {entity.address_count} {entity.address_count === 1 ? 'address' : 'addresses'}
                    {entity.jurisdiction && ` · ${entity.jurisdiction}`}
                  </>
                )}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
            {badges.map((badge) => (
              <span key={`${badge.label}-${badge.tone}`} style={badgeStyle(badge.tone)}>
                {badge.label}
              </span>
            ))}
            {isServiceInteraction ? (
              <>
                {activity.asset_symbol && (
                  <span style={{ ...badgeStyle('#2563eb'), textTransform: 'uppercase' }}>
                    {activity.asset_symbol}
                  </span>
                )}
                {activity.status && (
                  <span style={{ ...badgeStyle('#7c3aed'), textTransform: 'uppercase' }}>
                    {activity.status}
                  </span>
                )}
              </>
            ) : (
              <span style={{ ...badgeStyle(riskColor(entity.risk_score)), textTransform: 'uppercase' }}>
                {riskLabel(entity.risk_score)}
              </span>
            )}
          </div>
          {isServiceInteraction && (
            <div style={{ color: '#334155', marginTop: 10, fontSize: 12, lineHeight: 1.45 }}>
              {activity.route_summary ?? 'Transaction-centric smart-contract interaction'}
            </div>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
