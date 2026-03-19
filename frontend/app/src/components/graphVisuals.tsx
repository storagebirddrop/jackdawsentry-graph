import type { CSSProperties, ReactNode } from 'react';

import type {
  AddressNodeData,
  BridgeHopData,
  InvestigationNode,
  SwapEventData,
  UTXONodeData,
} from '../types/graph';
import type { GraphAppearanceState } from './graphAppearance';

const CHAIN_COLORS: Record<string, string> = {
  bitcoin: '#f7931a',
  lightning: '#f2a900',
  ethereum: '#627eea',
  bsc: '#f0b90b',
  polygon: '#8247e5',
  arbitrum: '#28a0f0',
  optimism: '#ff0420',
  base: '#0052ff',
  avalanche: '#e84142',
  solana: '#14f195',
  tron: '#ff060a',
  xrp: '#25a768',
  injective: '#33c9ff',
  cosmos: '#5064fb',
  sui: '#6fbcf0',
  starknet: '#7f5af0',
};

type GlyphKind =
  | 'address'
  | 'entity'
  | 'service'
  | 'bridge'
  | 'swap'
  | 'utxo'
  | 'instruction'
  | 'cluster'
  | 'sanction'
  | 'mixer'
  | 'coinjoin';

export function getChainColor(chain?: string): string {
  if (!chain) return '#64748b';
  return CHAIN_COLORS[chain.toLowerCase()] ?? '#64748b';
}

export function shortHash(value: string, leading = 6, trailing = 4): string {
  if (value.length <= leading + trailing + 1) return value;
  return `${value.slice(0, leading)}...${value.slice(-trailing)}`;
}

export function formatUsd(value?: number): string | null {
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: value >= 100 ? 0 : 2,
  }).format(value);
}

export function formatNative(value?: number, assetSymbol?: string): string | null {
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: value >= 100 ? 2 : 6,
  }).format(value);
  return assetSymbol ? `${formatted} ${assetSymbol}` : formatted;
}

export function formatTimestamp(timestamp?: string, includeTime = false): string | null {
  if (!timestamp) return null;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;

  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined,
    ...(includeTime ? { hour: 'numeric', minute: '2-digit' } : {}),
  }).format(date);
}

export function riskColor(score?: number): string {
  if (score === undefined) return '#64748b';
  if (score >= 0.7) return '#ef4444';
  if (score >= 0.4) return '#f59e0b';
  return '#22c55e';
}

export function riskLabel(score?: number): string {
  if (score === undefined) return 'unknown';
  if (score >= 0.7) return 'high';
  if (score >= 0.4) return 'medium';
  return 'low';
}

export function inferNodeChain(node: InvestigationNode): string | undefined {
  switch (node.node_type) {
    case 'address':
      return (node.address_data ?? node.node_data as AddressNodeData).chain;
    case 'service':
      return (node.node_data as { chain?: string }).chain;
    case 'bridge_hop':
      return (node.node_data as BridgeHopData).source_chain;
    case 'swap_event':
      return (node.node_data as SwapEventData).chain;
    case 'utxo':
      return 'bitcoin';
    case 'solana_instruction':
      return 'solana';
    default:
      return undefined;
  }
}

export function nodeAccentColor(
  node: InvestigationNode,
  appearance: GraphAppearanceState,
  fallback: string,
): string {
  if (!appearance.useChainColors) return fallback;
  return getChainColor(inferNodeChain(node)) ?? fallback;
}

export function nodeGlyphKind(node: InvestigationNode): GlyphKind {
  if (node.node_type === 'address') {
    const address = (node.address_data ?? node.node_data) as AddressNodeData;
    if (address.is_sanctioned) return 'sanction';
    if (address.is_mixer) return 'mixer';
    if (address.is_coinjoin_halt) return 'coinjoin';
    return 'address';
  }
  if (node.node_type === 'bridge_hop') return 'bridge';
  if (node.node_type === 'swap_event') return 'swap';
  if (node.node_type === 'utxo') {
    const utxo = node.node_data as UTXONodeData;
    return utxo.is_coinjoin_halt ? 'coinjoin' : 'utxo';
  }
  if (node.node_type === 'solana_instruction') return 'instruction';
  if (node.node_type === 'cluster_summary') return 'cluster';
  if (node.node_type === 'service') return 'service';
  return 'entity';
}

export function semanticBadges(node: InvestigationNode): Array<{ label: string; tone: string }> {
  if (node.node_type === 'address') {
    const address = (node.address_data ?? node.node_data) as AddressNodeData;
    return [
      ...(address.is_sanctioned ? [{ label: 'Sanctioned', tone: '#dc2626' }] : []),
      ...(address.is_mixer ? [{ label: 'Mixer', tone: '#7c3aed' }] : []),
      ...(address.is_coinjoin_halt ? [{ label: 'CoinJoin', tone: '#b45309' }] : []),
      ...(address.entity_category ? [{ label: address.entity_category, tone: '#2563eb' }] : []),
    ];
  }
  if (node.node_type === 'entity' || node.node_type === 'service') {
    const category = (node.node_data as { category?: string; service_type?: string }).category
      ?? (node.node_data as { category?: string; service_type?: string }).service_type;
    return category ? [{ label: category, tone: '#2563eb' }] : [];
  }
  if (node.node_type === 'bridge_hop') {
    return [{ label: 'Bridge', tone: '#7c3aed' }];
  }
  if (node.node_type === 'swap_event') {
    return [{ label: 'Swap', tone: '#0f766e' }];
  }
  return [];
}

export function isNodeVisibleInView(node: InvestigationNode, viewMode: GraphAppearanceState['viewMode']): boolean {
  if (viewMode === 'hybrid') return true;

  if (viewMode === 'entities') {
    if (node.node_type === 'entity' || node.node_type === 'service') return true;
    if (node.node_type === 'bridge_hop' || node.node_type === 'swap_event') return true;
    if (node.node_type === 'cluster_summary') return true;
    if (node.node_type === 'address') {
      const address = (node.address_data ?? node.node_data) as AddressNodeData;
      return Boolean(
        address.entity_id ||
        address.entity_name ||
        address.label ||
        address.is_sanctioned ||
        address.is_mixer ||
        address.is_coinjoin_halt,
      );
    }
    return false;
  }

  return node.node_type !== 'entity' && node.node_type !== 'service';
}

export function glyphSurfaceStyle(accent: string): CSSProperties {
  return {
    width: 38,
    height: 38,
    minWidth: 38,
    borderRadius: 12,
    display: 'grid',
    placeItems: 'center',
    background: `linear-gradient(180deg, ${accent}22, ${accent}0c)`,
    border: `1px solid ${accent}55`,
    color: accent,
  };
}

export function badgeStyle(tone: string): CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '2px 8px',
    borderRadius: 999,
    border: `1px solid ${tone}44`,
    background: `${tone}16`,
    color: tone,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.01em',
  };
}

export function valueChipStyle(accent: string): CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    padding: '4px 8px',
    borderRadius: 10,
    background: '#07111f',
    border: `1px solid ${accent}2f`,
    color: '#dbeafe',
    fontSize: 11,
    fontWeight: 600,
  };
}

export function GraphGlyph({ kind, accent }: { kind: GlyphKind; accent: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
      {renderGlyph(kind, accent)}
    </svg>
  );
}

function renderGlyph(kind: GlyphKind, accent: string): ReactNode {
  const strokeProps = {
    stroke: accent,
    strokeWidth: 1.6,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  switch (kind) {
    case 'bridge':
      return (
        <>
          <path d="M3 6.25h3.1a2.2 2.2 0 0 1 0 4.4H3" {...strokeProps} />
          <path d="M15 11.75h-3.1a2.2 2.2 0 0 1 0-4.4H15" {...strokeProps} />
          <path d="M6.8 9h4.4" {...strokeProps} />
        </>
      );
    case 'swap':
      return (
        <>
          <path d="M3 6h9.75" {...strokeProps} />
          <path d="M10.5 3.5L13 6l-2.5 2.5" {...strokeProps} />
          <path d="M15 12H5.25" {...strokeProps} />
          <path d="M7.5 9.5L5 12l2.5 2.5" {...strokeProps} />
        </>
      );
    case 'entity':
    case 'service':
      return (
        <>
          <rect x="4" y="3" width="10" height="12" rx="2" {...strokeProps} />
          <path d="M7 6.5h.01M11 6.5h.01M7 9.5h.01M11 9.5h.01M7 12.5h4" {...strokeProps} />
        </>
      );
    case 'utxo':
      return (
        <>
          <ellipse cx="9" cy="5" rx="4.5" ry="2.5" {...strokeProps} />
          <path d="M4.5 5v5c0 1.4 2 2.5 4.5 2.5s4.5-1.1 4.5-2.5V5" {...strokeProps} />
        </>
      );
    case 'instruction':
      return (
        <>
          <path d="M4 5.5h10" {...strokeProps} />
          <path d="M4 9h10" {...strokeProps} />
          <path d="M4 12.5h7" {...strokeProps} />
        </>
      );
    case 'cluster':
      return (
        <>
          <rect x="3.5" y="4" width="4.5" height="4.5" rx="1" {...strokeProps} />
          <rect x="10" y="4" width="4.5" height="4.5" rx="1" {...strokeProps} />
          <rect x="6.75" y="10" width="4.5" height="4.5" rx="1" {...strokeProps} />
          <path d="M8 8.5l1 1.5M10 8.5L9 10" {...strokeProps} />
        </>
      );
    case 'sanction':
      return (
        <>
          <path d="M9 3l4.5 1.5v3.2c0 3.1-2 5.9-4.5 7.3c-2.5-1.4-4.5-4.2-4.5-7.3V4.5L9 3Z" {...strokeProps} />
          <path d="M9 6.1v3.2" {...strokeProps} />
          <circle cx="9" cy="11.9" r="0.8" fill={accent} />
        </>
      );
    case 'mixer':
      return (
        <>
          <path d="M4 5.5h10" {...strokeProps} />
          <path d="M5 9h8" {...strokeProps} />
          <path d="M4 12.5h10" {...strokeProps} />
          <path d="M6 4v10M12 4v10" {...strokeProps} />
        </>
      );
    case 'coinjoin':
      return (
        <>
          <circle cx="5" cy="6" r="1.7" {...strokeProps} />
          <circle cx="13" cy="6" r="1.7" {...strokeProps} />
          <circle cx="9" cy="12.5" r="1.7" {...strokeProps} />
          <path d="M6.5 7.1l1.4 2.3M11.5 7.1L10.1 9.4M7.6 11.1h2.8" {...strokeProps} />
        </>
      );
    case 'address':
    default:
      return (
        <>
          <path d="M9 2.8c2.6 0 4.7 2.1 4.7 4.6c0 3.1-3.3 6.6-4.2 7.5a.7.7 0 0 1-1 0c-.9-.9-4.2-4.4-4.2-7.5c0-2.5 2.1-4.6 4.7-4.6Z" {...strokeProps} />
          <circle cx="9" cy="7.4" r="1.5" {...strokeProps} />
        </>
      );
  }
}
