/**
 * AddressNode — custom React Flow node for blockchain addresses.
 */

import { useState, useRef, useEffect } from 'react';
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
  /** True while a background ingest job is running for this address. */
  isIngestPending?: boolean;
}

function shortAddr(addr: string): string {
  if (!addr || addr.length <= 18) return addr;
  return `${addr.slice(0, 10)}...${addr.slice(-8)}`;
}

export default function AddressNode({ data, selected }: NodeProps) {
  const d = data as unknown as AddressNodeComponentData;
  const addr = (d.address_data ?? d.node_data) as AddressNodeData;
  const appearance = d.appearance ?? DEFAULT_GRAPH_APPEARANCE;

  const [copied, setCopied] = useState(false);
  const [hoverPrev, setHoverPrev] = useState(false);
  const [hoverNext, setHoverNext] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
        copyTimeoutRef.current = null;
      }
    };
  }, []);

  function copyAddress() {
    if (!addr?.address) return;

    // Clear any existing timeout
    if (copyTimeoutRef.current) {
      clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = null;
    }

    navigator.clipboard.writeText(addr.address).then(() => {
      setCopied(true);
      copyTimeoutRef.current = setTimeout(() => {
        setCopied(false);
        copyTimeoutRef.current = null;
      }, 1500);
    }).catch(() => {
      // Fallback for browsers that don't support clipboard API
      const textArea = document.createElement('textarea');
      textArea.value = addr.address;
      document.body.appendChild(textArea);
      textArea.select();
      let success = false;
      try {
        success = document.execCommand('copy');
      } catch {
        success = false;
      }
      document.body.removeChild(textArea);

      if (success) {
        setCopied(true);
        copyTimeoutRef.current = setTimeout(() => {
          setCopied(false);
          copyTimeoutRef.current = null;
        }, 1500);
      } else {
        console.error('Failed to copy address: both clipboard API and fallback failed');
      }
    });
  }

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
  const hasEntityName = !!(d.entity_name ?? addr.entity_name);
  // Prefer node-level risk_score (set by enricher) over address_data copy.
  const risk = d.risk_score ?? addr.risk_score;
  const isUnknownRisk = risk === null || risk === undefined;

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
          {/* Header row: chain + risk */}
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0 }}>
              {/* Chain eyebrow with color dot */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 5, ...eyebrowStyle }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: chainColor,
                    flexShrink: 0,
                  }}
                />
                {chain.toUpperCase()}
              </div>

              {/* Title: entity name or clickable address */}
              <button
                type="button"
                title={copied ? 'Copied!' : `Click to copy ${addr.address}`}
                onClick={copyAddress}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    copyAddress();
                  }
                }}
                style={{
                  ...titleStyle,
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  cursor: 'pointer',
                  userSelect: 'none',
                  color: copied ? accent : '#0f172a',
                  transition: 'color 0.15s',
                  textAlign: 'left',
                  fontFamily: 'inherit',
                  fontSize: 'inherit',
                  fontWeight: 'inherit',
                  lineHeight: 'inherit',
                  marginTop: 'inherit',
                }}
              >
                {hasEntityName
                  ? (d.entity_name ?? addr.entity_name)
                  : shortAddr(addr.address)}
              </button>

              {/* Subtitle */}
              <div style={subtitleStyle}>
                {hasEntityName
                  ? <span style={monoStyle}>{shortAddr(addr.address)}</span>
                  : <span style={{ ...monoStyle, color: '#94a3b8' }}>
                      {chain.toUpperCase()} • {addr.address.slice(0, 8)}...
                    </span>
                }
              </div>
            </div>

            {/* Risk pill — subdued when unknown */}
            <div
              style={{
                ...riskPillStyle,
                color: isUnknownRisk ? '#94a3b8' : riskColor(risk),
                borderColor: isUnknownRisk ? '#e2e8f0' : `${riskColor(risk)}55`,
                background: isUnknownRisk ? '#f8fafc' : '#ffffff',
              }}
            >
              {isUnknownRisk ? 'unscored' : riskLabel(risk)}
            </div>
          </div>

          {/* Semantic badges */}
          {badges.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
              {badges.map((badge) => (
                <span key={`${badge.label}-${badge.tone}`} style={badgeStyle(badge.tone)}>
                  {badge.label}
                </span>
              ))}
            </div>
          )}

          {/* Ingest-pending banner */}
          {d.isIngestPending && (
            <div style={ingestPendingBannerStyle}>
              <span style={pulseStyle}>●</span>
              {' '}Fetching data…
            </div>
          )}

          {/* Bottom row: value + expand buttons */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {valueLabel && <span style={valueChipStyle}>{valueLabel}</span>}
              {(d.display_sublabel ?? addr.label) && (
                <span style={secondaryChipStyle}>{d.display_sublabel ?? addr.label}</span>
              )}
            </div>

            <div style={{ display: 'flex', gap: 5 }}>
              {d.onExpandPrev && d.expandable_directions.includes('prev') && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); d.onExpandPrev?.(); }}
                  onMouseEnter={() => setHoverPrev(true)}
                  onMouseLeave={() => setHoverPrev(false)}
                  style={{
                    ...actionButtonBase,
                    background: hoverPrev ? '#f1f5f9' : '#ffffff',
                    color: hoverPrev ? '#0f172a' : '#475569',
                  }}
                >
                  ← Prev
                </button>
              )}
              {d.onExpandNext && d.expandable_directions.includes('next') && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); d.onExpandNext?.(); }}
                  onMouseEnter={() => setHoverNext(true)}
                  onMouseLeave={() => setHoverNext(false)}
                  disabled={d.isExpanding}
                  style={{
                    ...actionButtonBase,
                    background: d.isExpanding
                      ? '#dbeafe'
                      : hoverNext ? accent : `${accent}18`,
                    color: d.isExpanding
                      ? '#3b82f6'
                      : hoverNext ? '#ffffff' : accent,
                    borderColor: `${accent}55`,
                    opacity: d.isExpanding ? 0.8 : 1,
                  }}
                >
                  {d.isExpanding
                    ? <span style={pulseStyle}>●</span>
                    : 'Next →'}
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
  minWidth: 260,
  maxWidth: 300,
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
  textTransform: 'uppercase' as const,
};

const titleStyle: React.CSSProperties = {
  color: '#0f172a',
  fontSize: 15,
  lineHeight: 1.2,
  fontWeight: 700,
  marginTop: 3,
};

const subtitleStyle: React.CSSProperties = {
  marginTop: 3,
};

const monoStyle: React.CSSProperties = {
  fontFamily: '"IBM Plex Mono", "Fira Code", monospace',
  fontSize: 11,
  color: '#64748b',
};

const riskPillStyle: React.CSSProperties = {
  padding: '3px 8px',
  borderRadius: 999,
  border: '1px solid',
  fontSize: 10,
  fontWeight: 700,
  textTransform: 'uppercase' as const,
  whiteSpace: 'nowrap' as const,
  letterSpacing: '0.05em',
};

const valueChipStyle: React.CSSProperties = {
  padding: '4px 8px',
  borderRadius: 10,
  background: '#eff6ff',
  border: '1px solid #bfdbfe',
  color: '#1d4ed8',
  fontSize: 11,
  fontWeight: 700,
};

const secondaryChipStyle: React.CSSProperties = {
  padding: '4px 8px',
  borderRadius: 10,
  background: '#f8fafc',
  border: '1px solid #e2e8f0',
  color: '#334155',
  fontSize: 11,
  fontWeight: 600,
};

const actionButtonBase: React.CSSProperties = {
  border: '1px solid #e2e8f0',
  borderRadius: 10,
  padding: '5px 10px',
  fontSize: 11,
  fontWeight: 700,
  cursor: 'pointer',
  transition: 'background 0.12s, color 0.12s',
  outline: 'none',
};

const pulseStyle: React.CSSProperties = {
  display: 'inline-block',
  animation: 'pulse 1s ease-in-out infinite',
};

const ingestPendingBannerStyle: React.CSSProperties = {
  marginTop: 10,
  padding: '4px 10px',
  borderRadius: 8,
  background: '#eff6ff',
  border: '1px solid #bfdbfe',
  color: '#1d4ed8',
  fontSize: 11,
  fontWeight: 600,
  display: 'flex',
  alignItems: 'center',
  gap: 5,
};
