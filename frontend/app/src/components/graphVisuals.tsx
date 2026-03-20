import type { CSSProperties, ReactNode } from 'react';

import type {
  AddressNodeData,
  AtomicSwapData,
  BtcSidechainPegData,
  BridgeHopData,
  InvestigationNode,
  LightningChannelCloseData,
  LightningChannelOpenData,
  ServiceNodeData,
  SolanaInstructionData,
  SwapEventData,
  UTXONodeData,
} from '../types/graph';
import type { GraphAppearanceState } from './graphAppearance';

const CHAIN_COLORS: Record<string, string> = {
  bitcoin: '#f7931a',
  lightning: '#f2a900',
  liquid: '#12b3a8',
  rootstock: '#f97316',
  stacks: '#5546ff',
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

const BRIDGE_PROTOCOL_META: Record<
  string,
  { color: string; label: string; family: 'native' | 'lock' | 'solver' | 'liquidity' | 'burn' }
> = {
  thorchain: { color: '#16a34a', label: 'THORChain', family: 'native' },
  chainflip: { color: '#ea580c', label: 'Chainflip', family: 'native' },
  wormhole: { color: '#7c3aed', label: 'Wormhole', family: 'lock' },
  debridge: { color: '#2563eb', label: 'deBridge', family: 'solver' },
  mayan: { color: '#0891b2', label: 'Mayan', family: 'solver' },
  squid: { color: '#0284c7', label: 'Squid', family: 'solver' },
  lifi: { color: '#db2777', label: 'LI.FI', family: 'solver' },
  across: { color: '#0f766e', label: 'Across', family: 'liquidity' },
  celer: { color: '#dc2626', label: 'Celer', family: 'burn' },
  stargate: { color: '#7c2d12', label: 'Stargate', family: 'liquidity' },
  synapse: { color: '#9333ea', label: 'Synapse', family: 'burn' },
};

const SWAP_PROTOCOL_META: Record<string, { color: string; label: string }> = {
  uniswap: { color: '#ff007a', label: 'Uniswap' },
  sushiswap: { color: '#7c3aed', label: 'SushiSwap' },
  jupiter: { color: '#14f195', label: 'Jupiter' },
  raydium: { color: '#38bdf8', label: 'Raydium' },
  orca: { color: '#0ea5e9', label: 'Orca' },
  curve: { color: '#2563eb', label: 'Curve' },
  balancer: { color: '#475569', label: 'Balancer' },
  oneinch: { color: '#1d4ed8', label: '1inch' },
  paraswap: { color: '#0f766e', label: 'ParaSwap' },
  kyberswap: { color: '#10b981', label: 'KyberSwap' },
  pancakeswap: { color: '#f59e0b', label: 'PancakeSwap' },
};

const SOLANA_PROGRAM_META: Record<string, { color: string; label: string }> = {
  ['tokenkegqfezyinwajbnbgkpfxcwubvf9ss623vq5da']: { color: '#14f195', label: 'SPL Token' },
  ['jup6lkbzbjs1jkkwapdhny74zcz3tluzoi5qnyvtav4']: { color: '#14f195', label: 'Jupiter v6' },
  ['675kpx9mhtjs2zt1qfr1nyhuzelxfqm9h24wfsut1mp8']: { color: '#38bdf8', label: 'Raydium AMM' },
  ['whirlbmiicvdio4qvufm5kag6ct8vwpyzgff3sfkdw6']: { color: '#0ea5e9', label: 'Orca Whirlpool' },
  ['worm2zog2kud4vfxhvjh93uuh596ayrfgq2mgjnmtth']: { color: '#7c3aed', label: 'Wormhole' },
  ['mayanu2ys5r3fuboprkmhtcm9e4mnr7txbmvzs2kn3k']: { color: '#0891b2', label: 'Mayan' },
};

const SIDECHAIN_META: Record<string, { color: string; label: string }> = {
  liquid: { color: '#12b3a8', label: 'Liquid' },
  rootstock: { color: '#f97316', label: 'Rootstock' },
  stacks: { color: '#5546ff', label: 'Stacks' },
};

const SEMANTIC_PALETTE = [
  '#2563eb',
  '#7c3aed',
  '#0f766e',
  '#ea580c',
  '#db2777',
  '#0891b2',
  '#b45309',
  '#475569',
] as const;

export interface NodeSemanticMeta {
  key: string;
  label: string;
  family: string;
  color: string;
}

type GlyphKind =
  | 'address'
  | 'entity'
  | 'service'
  | 'bridge'
  | 'swap'
  | 'lightning'
  | 'peg'
  | 'atomic'
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

export function getBridgeProtocolColor(protocolId?: string): string {
  if (!protocolId) return '#7c3aed';
  return BRIDGE_PROTOCOL_META[protocolId.toLowerCase()]?.color ?? '#7c3aed';
}

export function bridgeProtocolLabel(protocolId?: string): string {
  if (!protocolId) return 'Unknown bridge';
  return BRIDGE_PROTOCOL_META[protocolId.toLowerCase()]?.label ?? protocolId;
}

function normalizeSemanticKey(value?: string): string {
  return (value ?? 'unknown').trim().toLowerCase();
}

function semanticColorFromKey(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 33 + key.charCodeAt(i)) >>> 0;
  }
  return SEMANTIC_PALETTE[hash % SEMANTIC_PALETTE.length];
}

export function titleCaseIdentifier(value?: string): string {
  if (!value) return 'Unknown';
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .map((part) => (part.length <= 4 ? part.toUpperCase() : `${part[0].toUpperCase()}${part.slice(1)}`))
    .join(' ');
}

export function swapProtocolLabel(protocolId?: string): string {
  if (!protocolId) return 'Unknown swap';
  return SWAP_PROTOCOL_META[normalizeSemanticKey(protocolId)]?.label ?? titleCaseIdentifier(protocolId);
}

export function getSwapProtocolColor(protocolId?: string): string {
  if (!protocolId) return '#0f766e';
  const key = normalizeSemanticKey(protocolId);
  return SWAP_PROTOCOL_META[key]?.color ?? semanticColorFromKey(`swap:${key}`);
}

export function sidechainLabel(sidechain?: string): string {
  if (!sidechain) return 'Bitcoin sidechain';
  return SIDECHAIN_META[normalizeSemanticKey(sidechain)]?.label ?? titleCaseIdentifier(sidechain);
}

export function sidechainColor(sidechain?: string): string {
  if (!sidechain) return '#0f766e';
  const key = normalizeSemanticKey(sidechain);
  return SIDECHAIN_META[key]?.color ?? getChainColor(sidechain);
}

export function solanaProgramLabel(programId?: string, programName?: string): string {
  if (programName) return programName;
  if (!programId) return 'Unknown program';
  return SOLANA_PROGRAM_META[normalizeSemanticKey(programId)]?.label
    ?? `${programId.slice(0, 6)}…${programId.slice(-4)}`;
}

export function solanaProgramColor(programId?: string): string {
  if (!programId) return '#9945ff';
  const key = normalizeSemanticKey(programId);
  return SOLANA_PROGRAM_META[key]?.color ?? semanticColorFromKey(`solana:${key}`);
}

export function serviceProtocolLabel(protocolId?: string, serviceType?: string): string {
  if (protocolId) return titleCaseIdentifier(protocolId);
  if (serviceType) return titleCaseIdentifier(serviceType);
  return 'Service activity';
}

export function serviceProtocolColor(protocolId?: string, serviceType?: string): string {
  const key = normalizeSemanticKey(protocolId ?? serviceType);
  if (key === 'unknown') return '#2563eb';
  return semanticColorFromKey(`service:${key}`);
}

export function bridgeMechanismLabel(mechanism?: string): string {
  switch ((mechanism ?? '').toLowerCase()) {
    case 'native_amm':
      return 'Native rail';
    case 'lock_mint':
      return 'Lock and mint';
    case 'burn_release':
      return 'Burn and release';
    case 'solver':
      return 'Solver filled';
    case 'liquidity':
      return 'Liquidity relay';
    default:
      return mechanism ? mechanism.replace(/_/g, ' ') : 'Unknown mechanism';
  }
}

export function bridgeStatusTone(status?: string): string {
  if (status === 'completed') return '#10b981';
  if (status === 'failed') return '#ef4444';
  return '#f59e0b';
}

export function bridgeRouteLabel(hop: Partial<BridgeHopData>): string {
  return `${(hop.source_chain ?? '?').toUpperCase()} -> ${(hop.destination_chain ?? 'pending').toUpperCase()}`;
}

export function bridgeAssetRouteLabel(hop: Partial<BridgeHopData>): string {
  return `${hop.source_asset ?? '?'} -> ${hop.destination_asset ?? (hop.destination_chain ? '?' : 'pending')}`;
}

export function shortHash(value: string, leading = 6, trailing = 4): string {
  if (value.length <= leading + trailing + 3) return value;
  return `${value.slice(0, leading)}...${value.slice(-trailing)}`;
}

export function formatUsd(value?: number): string | null {
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2,
  }).format(value);
}

export function formatNative(value?: number, assetSymbol?: string): string | null {
  if (value === undefined || value === null || Number.isNaN(value)) return null;
  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 6,
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
    case 'lightning_channel_open':
    case 'lightning_channel_close':
      return 'lightning';
    case 'btc_sidechain_peg_in':
    case 'btc_sidechain_peg_out': {
      const peg = (node.btc_sidechain_peg_data ?? node.node_data) as
        | BtcSidechainPegData
        | undefined;
      return peg?.sidechain ?? 'bitcoin';
    }
    case 'atomic_swap': {
      const swap = (node.atomic_swap_data ?? node.node_data) as
        | AtomicSwapData
        | undefined;
      return swap?.source_chain ?? node.chain;
    }
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

export function semanticMetaForNode(node: InvestigationNode): NodeSemanticMeta | null {
  if (node.node_type === 'bridge_hop') {
    const hop = (node.bridge_hop_data ?? node.node_data) as BridgeHopData;
    return {
      key: `bridge:${normalizeSemanticKey(hop.protocol_id)}`,
      label: bridgeProtocolLabel(hop.protocol_id),
      family: 'Bridge',
      color: getBridgeProtocolColor(hop.protocol_id),
    };
  }

  if (node.node_type === 'swap_event') {
    const swap = (node.swap_event_data ?? node.node_data) as SwapEventData;
    return {
      key: `swap:${normalizeSemanticKey(swap.protocol_id)}`,
      label: swapProtocolLabel(swap.protocol_id),
      family: 'Swap',
      color: getSwapProtocolColor(swap.protocol_id),
    };
  }

  if (node.node_type === 'atomic_swap') {
    const swap = (node.atomic_swap_data ?? node.node_data) as AtomicSwapData | undefined;
    return {
      key: `atomic:${normalizeSemanticKey(swap?.protocol_id ?? 'htlc')}`,
      label: swap?.protocol_id ? titleCaseIdentifier(swap.protocol_id) : 'HTLC',
      family: 'Atomic swap',
      color: swap?.protocol_id
        ? semanticColorFromKey(`atomic:${normalizeSemanticKey(swap.protocol_id)}`)
        : '#2563eb',
    };
  }

  if (node.node_type === 'lightning_channel_open' || node.node_type === 'lightning_channel_close') {
    return {
      key: 'lightning:network',
      label: 'Lightning',
      family: 'Lightning',
      color: getChainColor('lightning'),
    };
  }

  if (node.node_type === 'btc_sidechain_peg_in' || node.node_type === 'btc_sidechain_peg_out') {
    const peg = (node.btc_sidechain_peg_data ?? node.node_data) as BtcSidechainPegData | undefined;
    return {
      key: `peg:${normalizeSemanticKey(peg?.sidechain)}`,
      label: sidechainLabel(peg?.sidechain),
      family: 'Sidechain peg',
      color: sidechainColor(peg?.sidechain),
    };
  }

  if (node.node_type === 'solana_instruction') {
    const ix = (node.instruction_data ?? node.node_data) as SolanaInstructionData | undefined;
    return {
      key: `solana:${normalizeSemanticKey(ix?.program_id ?? ix?.program_name)}`,
      label: solanaProgramLabel(ix?.program_id, ix?.program_name),
      family: 'Solana program',
      color: solanaProgramColor(ix?.program_id),
    };
  }

  if (node.node_type === 'service') {
    const service = (node.service_data ?? node.node_data) as ServiceNodeData | undefined;
    const protocolId = node.activity_summary?.protocol_id ?? service?.protocol_id;
    const serviceType = service?.service_type ?? node.activity_summary?.activity_type;
    return {
      key: `service:${normalizeSemanticKey(protocolId ?? serviceType)}`,
      label: serviceProtocolLabel(protocolId, serviceType),
      family: 'Service',
      color: serviceProtocolColor(protocolId, serviceType),
    };
  }

  if (node.node_type === 'address') {
    const address = (node.address_data ?? node.node_data) as AddressNodeData;
    if (address.is_mixer) {
      return { key: 'exposure:mixer', label: 'Mixer', family: 'Exposure', color: '#7c3aed' };
    }
    if (address.is_coinjoin_halt) {
      return { key: 'bitcoin:coinjoin', label: 'CoinJoin', family: 'Bitcoin', color: '#b45309' };
    }
    if (address.is_sanctioned) {
      return { key: 'exposure:sanctioned', label: 'Sanctioned', family: 'Exposure', color: '#dc2626' };
    }
  }

  if (node.node_type === 'utxo') {
    const utxo = (node.utxo_data ?? node.node_data) as UTXONodeData | undefined;
    if (utxo?.is_coinjoin_halt) {
      return { key: 'bitcoin:coinjoin', label: 'CoinJoin', family: 'Bitcoin', color: '#b45309' };
    }
  }

  return null;
}

export function nodeSemanticAccentColor(
  node: InvestigationNode,
  appearance: GraphAppearanceState,
  fallback: string,
): string {
  return semanticMetaForNode(node)?.color ?? nodeAccentColor(node, appearance, fallback);
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
  if (node.node_type === 'lightning_channel_open' || node.node_type === 'lightning_channel_close') {
    return 'lightning';
  }
  if (node.node_type === 'btc_sidechain_peg_in' || node.node_type === 'btc_sidechain_peg_out') {
    return 'peg';
  }
  if (node.node_type === 'atomic_swap') return 'atomic';
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
  if (node.node_type === 'lightning_channel_open') {
    const channel = (node.lightning_channel_open_data ?? node.node_data) as
      | LightningChannelOpenData
      | undefined;
    return [
      { label: 'Lightning', tone: '#f2a900' },
      ...(channel?.is_private !== undefined
        ? [{ label: channel.is_private ? 'Private' : 'Public', tone: '#2563eb' }]
        : []),
    ];
  }
  if (node.node_type === 'lightning_channel_close') {
    const channel = (node.lightning_channel_close_data ?? node.node_data) as
      | LightningChannelCloseData
      | undefined;
    return [
      { label: 'Lightning', tone: '#f2a900' },
      { label: 'Closed', tone: '#475569' },
      ...(channel?.close_type ? [{ label: channel.close_type, tone: '#b45309' }] : []),
    ];
  }
  if (node.node_type === 'btc_sidechain_peg_in' || node.node_type === 'btc_sidechain_peg_out') {
    const peg = (node.btc_sidechain_peg_data ?? node.node_data) as
      | BtcSidechainPegData
      | undefined;
    const directionLabel = node.node_type === 'btc_sidechain_peg_in' ? 'Peg In' : 'Peg Out';
    return [
      { label: directionLabel, tone: '#0f766e' },
      ...(peg?.sidechain ? [{ label: peg.sidechain, tone: getChainColor(peg.sidechain) }] : []),
    ];
  }
  if (node.node_type === 'atomic_swap') {
    const swap = (node.atomic_swap_data ?? node.node_data) as AtomicSwapData | undefined;
    return [
      { label: 'Atomic Swap', tone: '#2563eb' },
      ...(swap?.state ? [{ label: swap.state, tone: '#7c3aed' }] : []),
      ...(swap?.protocol_id ? [{ label: swap.protocol_id, tone: '#0f766e' }] : []),
    ];
  }
  return [];
}

export function isNodeVisibleInView(node: InvestigationNode, viewMode: GraphAppearanceState['viewMode']): boolean {
  if (viewMode === 'hybrid') return true;

  if (viewMode === 'entities') {
    if (node.node_type === 'entity' || node.node_type === 'service') return true;
    if (
      node.node_type === 'bridge_hop' ||
      node.node_type === 'swap_event' ||
      node.node_type === 'lightning_channel_open' ||
      node.node_type === 'lightning_channel_close' ||
      node.node_type === 'btc_sidechain_peg_in' ||
      node.node_type === 'btc_sidechain_peg_out' ||
      node.node_type === 'atomic_swap'
    ) {
      return true;
    }
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

export function withAlpha(hexColor: string, alphaHex: string): string {
  // Simple hex alpha concatenation, assuming 6-char hex input
  // In a real app, this should handle 3-char, rgb, etc.
  if (hexColor.startsWith('#') && hexColor.length === 7) {
    return `${hexColor}${alphaHex}`;
  }
  return hexColor;
}

export function glyphSurfaceStyle(accent: string): CSSProperties {
  return {
    width: 38,
    height: 38,
    minWidth: 38,
    borderRadius: 12,
    display: 'grid',
    placeItems: 'center',
    background: `linear-gradient(180deg, ${withAlpha(accent, '22')}, ${withAlpha(accent, '0c')})`,
    border: `1px solid ${withAlpha(accent, '55')}`,
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
    case 'lightning':
      return (
        <>
          <path d="M9.8 2.8L5.9 8.4h2.8L7.9 15.2l4.2-6h-2.8l.5-6.4Z" {...strokeProps} />
        </>
      );
    case 'peg':
      return (
        <>
          <path d="M3.5 9h3.25" {...strokeProps} />
          <path d="M11.25 9H14.5" {...strokeProps} />
          <path d="M5.75 6.25L8.9 9l-3.15 2.75" {...strokeProps} />
          <path d="M12.25 5.2v7.6" {...strokeProps} />
          <path d="M10.7 6.7h3.1" {...strokeProps} />
        </>
      );
    case 'atomic':
      return (
        <>
          <rect x="3.8" y="4.3" width="4.1" height="4.1" rx="1" {...strokeProps} />
          <rect x="10.1" y="9.6" width="4.1" height="4.1" rx="1" {...strokeProps} />
          <path d="M7.9 6.35h2.1l2.05 2.05v1.2" {...strokeProps} />
          <path d="M9.2 12.9H7.1L5 10.8V9.6" {...strokeProps} />
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
