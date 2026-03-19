/**
 * AddressNode — custom React Flow node for blockchain addresses.
 *
 * Displays short address, attribution, risk, semantic badges, value context,
 * and quick expansion affordances.
 */

import { Handle, Position, type NodeProps } from '@xyflow/react';

import type { InvestigationNode, AddressNodeData } from '../../types/graph';
import {
  DEFAULT_GRAPH_APPEARANCE,
  type GraphAppearanceState,
} from '../graphAppearance';
import {
  badgeStyle,
  formatUsd,
  getChainColor,
  GraphGlyph,
  glyphSurfaceStyle,
  nodeAccentColor,
  nodeGlyphKind,
  riskColor,
  riskLabel,
  semanticBadges,
} from '../graphVisuals';

interface AddressNodeComponentData extends InvestigationNode {
  branch_color: string;
  appearance?: GraphAppearanceState;
  onExpandNext?: () => void;
  onExpandPrev?: () => void;
  isExpanding?: boolean;
}

function shortAddr(addr: string): string {
  if (addr.length <= 16) return addr;
  return `${addr.slice(0, 8)}...${addr.slice(-6)}`;
}

export default function AddressNode({ data, selected }: NodeProps) {
  const d = data as unknown as AddressNodeComponentData;
  const addr = (d.address_data ?? d.node_data) as AddressNodeData;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;

  if (!addr?.address) {
    return (
      <div style={{ ...cardStyle, borderColor: '#ef4444' }}>
        <Handle type="source" position={Position.Right} />
        <Handle type="target" position={Position.Left} />
        <div style={{ color: '#b91c1c', fontWeight: 700 }}>Invalid address data</div>
      </div>
    );
  }

  const chain = addr.chain ?? d.chain ?? 'unknown';
  const chainColor = getChainColor(chain);
  const accent = nodeAccentColor(d, appearance, d.branch_color ?? chainColor);
  const valueLabel = appearance.showValues ? formatUsd(addr.fiat_value_usd) : null;
  const badges = semanticBadges(d);

  return (
    <div
      style={{
        ...cardStyle,
        borderColor: selected ? accent : `${accent}90`,
        boxShadow: selected
          ? `0 18px 34px ${accent}25`
          : '0 14px 28px rgba(15, 23, 42, 0.08)',
      }}
    >
      <Handle type="target" position={Position.Left} />

      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {appearance.showEntityIcons && (
          <div style={glyphSurfaceStyle(accent)}>
            <GraphGlyph kind={nodeGlyphKind(d)} accent={accent} />
          </div>
        )}

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0 }}>
              <div style={eyebrowStyle}>{chain}</div>
              <div style={titleStyle}>{d.entity_name ?? addr.entity_name ?? shortAddr(addr.address)}</div>
              <div style={subtitleStyle}>{(d.entity_name ?? addr.entity_name) ? shortAddr(addr.address) : 'unattributed address'}</div>
            </div>
            <div style={{ ...riskPillStyle, color: riskColor(addr.risk_score), borderColor: `${riskColor(addr.risk_score)}55` }}>
              {riskLabel(addr.risk_score)}
            </div>
          </div>

          {badges.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {badges.map((badge) => (
                <span key={`${badge.label}-${badge.tone}`} style={badgeStyle(badge.tone)}>
                  {badge.label}
                </span>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {valueLabel && <span style={valueChipStyle}>{valueLabel}</span>}
              {(d.display_sublabel ?? addr.label) && <span style={secondaryChipStyle}>{d.display_sublabel ?? addr.label}</span>}
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {d.onExpandPrev && d.expandable_directions.includes('prev') && (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    d.onExpandPrev?.();
                  }}
                  style={actionButtonStyle}
                >
                  Prev
                </button>
              )}
              {d.onExpandNext && d.expandable_directions.includes('next') && (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    d.onExpandNext?.();
                  }}
                  style={actionButtonStyle}
                >
                  {d.isExpanding ? '...' : 'Next'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  minWidth: 250,
  maxWidth: 290,
  padding: 14,
  borderRadius: 18,
  background: 'rgba(255,255,255,0.97)',
  border: '1px solid rgba(148, 163, 184, 0.3)',
  fontFamily: '"IBM Plex Sans", "Segoe UI", sans-serif',
};

const eyebrowStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 10,
  fontWeight: 800,
  letterSpacing: '0.08em',
  textTransform: 'uppercase',
};

const titleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 16,
  lineHeight: 1.15,
  fontWeight: 700,
  marginTop: 3,
};

const subtitleStyle: React.CSSProperties = {
  color: '#64748b',
  fontSize: 12,
  marginTop: 4,
};

const riskPillStyle: React.CSSProperties = {
  padding: '4px 8px',
  borderRadius: 999,
  background: '#ffffff',
  border: '1px solid',
  fontSize: 10,
  fontWeight: 800,
  textTransform: 'uppercase',
  whiteSpace: 'nowrap',
};

const valueChipStyle: React.CSSProperties = {
  padding: '5px 8px',
  borderRadius: 10,
  background: '#eff6ff',
  border: '1px solid #bfdbfe',
  color: '#1d4ed8',
  fontSize: 11,
  fontWeight: 700,
};

const secondaryChipStyle: React.CSSProperties = {
  padding: '5px 8px',
  borderRadius: 10,
  background: '#f8fafc',
  border: '1px solid #e2e8f0',
  color: '#334155',
  fontSize: 11,
  fontWeight: 600,
};

const actionButtonStyle: React.CSSProperties = {
  border: '1px solid #cbd5e1',
  background: '#ffffff',
  color: '#334155',
  borderRadius: 10,
  padding: '6px 10px',
  fontSize: 11,
  fontWeight: 700,
  cursor: 'pointer',
};
