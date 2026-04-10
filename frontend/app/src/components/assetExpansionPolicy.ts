import type {
  AssetOption,
  AssetSelector,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';
import { normalizeInvestigationNodeId } from '../types/graph';

const NATIVE_ASSET_SYMBOLS: Record<string, string> = {
  bitcoin: 'BTC',
  solana: 'SOL',
  tron: 'TRX',
  ethereum: 'ETH',
  bsc: 'BNB',
  polygon: 'MATIC',
  arbitrum: 'ETH',
  base: 'ETH',
  avalanche: 'AVAX',
  optimism: 'ETH',
  starknet: 'ETH',
  injective: 'INJ',
};

function normalizeChain(chain?: string | null): string {
  return (chain ?? '').trim().toLowerCase();
}

function assetScopeEligibleChain(node: Pick<InvestigationNode, 'chain' | 'address_data'>): string {
  return normalizeChain(node.chain ?? node.address_data?.chain);
}

function assetSelectorIdentity(
  option: Pick<AssetSelector, 'mode' | 'chain' | 'chain_asset_id' | 'asset_symbol' | 'canonical_asset_id'>,
): string {
  const chain = normalizeChain(option.chain);
  if (option.mode === 'all') {
    return `all:${chain}`;
  }
  if (option.mode === 'native') {
    return [
      'native',
      chain,
      '',
      option.asset_symbol ?? '',
      option.canonical_asset_id ?? '',
    ].join(':');
  }
  return [
    'asset',
    chain,
    option.chain_asset_id ?? '',
    option.asset_symbol ?? '',
    option.canonical_asset_id ?? '',
  ].join(':');
}

export function assetOptionKey(
  option: Pick<AssetSelector, 'mode' | 'chain' | 'chain_asset_id' | 'asset_symbol' | 'canonical_asset_id'>,
): string {
  return assetSelectorIdentity(option);
}

export function normalizeAssetSelectors(
  selectors: readonly AssetSelector[] | null | undefined,
): AssetSelector[] {
  if (!selectors || selectors.length === 0) {
    return [];
  }

  const normalized = new Map<string, AssetSelector>();
  for (const selector of selectors) {
    const chain = normalizeChain(selector.chain);
    if (!chain || selector.mode === 'all') {
      continue;
    }

    const nextSelector: AssetSelector = {
      mode: selector.mode,
      chain,
      chain_asset_id: selector.chain_asset_id,
      asset_symbol: selector.asset_symbol,
      canonical_asset_id: selector.canonical_asset_id,
    };
    normalized.set(assetSelectorIdentity(nextSelector), nextSelector);
  }

  return [...normalized.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, selector]) => selector);
}

export function isAssetScopeEligibleNode(
  node: Pick<InvestigationNode, 'node_id' | 'node_type' | 'chain' | 'address_data'>,
): boolean {
  return node.node_type === 'address' && assetScopeEligibleChain(node) !== 'bitcoin';
}

export function sanitizeNodeAssetScopes(
  rawScopes: Record<string, AssetSelector[]> | null | undefined,
  nodes: Iterable<Pick<InvestigationNode, 'node_id' | 'node_type' | 'chain' | 'address_data'>>,
): Record<string, AssetSelector[]> {
  if (!rawScopes) {
    return {};
  }

  const eligibleNodes = new Map<
    string,
    Pick<InvestigationNode, 'node_id' | 'node_type' | 'chain' | 'address_data'>
  >();
  for (const node of nodes) {
    eligibleNodes.set(normalizeInvestigationNodeId(node.node_id), node);
  }

  const sanitized: Record<string, AssetSelector[]> = {};
  for (const [nodeId, selectors] of Object.entries(rawScopes)) {
    if (!Array.isArray(selectors)) {
      continue;
    }

    const normalizedNodeId = normalizeInvestigationNodeId(nodeId);
    const node = eligibleNodes.get(normalizedNodeId);
    if (!node || !isAssetScopeEligibleNode(node)) {
      continue;
    }
    sanitized[normalizedNodeId] = normalizeAssetSelectors(selectors);
  }

  return sanitized;
}

export function getNodeAssetScopeSelectors(
  nodeId: string,
  nodeAssetScopes: ReadonlyMap<string, readonly AssetSelector[]>,
): AssetSelector[] {
  return [...(nodeAssetScopes.get(nodeId) ?? [])];
}

export function getSelectedAssetOptionKeysForNode(
  nodeId: string,
  nodeAssetScopes: ReadonlyMap<string, readonly AssetSelector[]>,
  optionsByNodeId: ReadonlyMap<string, AssetOption[]>,
): string[] {
  const selectors = getNodeAssetScopeSelectors(nodeId, nodeAssetScopes);
  if (selectors.length === 0) {
    return [];
  }

  const selectedKeys = new Set(selectors.map((selector) => assetSelectorIdentity(selector)));
  return (optionsByNodeId.get(nodeId) ?? [])
    .filter((option) => selectedKeys.has(assetSelectorIdentity(option)))
    .map((option) => assetOptionKey(option))
    .sort();
}

export function getStoredNodeAssetSelectors(
  nodeId: string,
  selectedKeysByNodeId: ReadonlyMap<string, readonly string[]>,
  optionsByNodeId: ReadonlyMap<string, AssetOption[]>,
): AssetSelector[] {
  const keys = selectedKeysByNodeId.get(nodeId);
  if (!keys || keys.length === 0) {
    return [];
  }
  const options = optionsByNodeId.get(nodeId) ?? [];
  const optionByKey = new Map(options.map((option) => [assetOptionKey(option), option]));
  return Array.from(new Set(keys.filter((key) => !key.startsWith('all:'))))
    .sort()
    .flatMap((key) => {
      const option = optionByKey.get(key);
      if (!option) {
        return [];
      }
      const { display_label: _label, ...selector } = option;
      return [selector];
    });
}

export function deriveEdgeTraceAssetSelector(
  edge: Pick<InvestigationEdge, 'tx_chain' | 'asset_symbol' | 'canonical_asset_id' | 'chain_asset_id'>,
  fallbackChain?: string,
): AssetSelector | null {
  const chain = normalizeChain(edge.tx_chain ?? fallbackChain);
  if (!chain) {
    return null;
  }

  if (edge.chain_asset_id) {
    return {
      mode: 'asset',
      chain,
      chain_asset_id: edge.chain_asset_id,
      asset_symbol: edge.asset_symbol,
      canonical_asset_id: edge.canonical_asset_id,
    };
  }

  const nativeSymbol = NATIVE_ASSET_SYMBOLS[chain];
  if (
    nativeSymbol
    && edge.asset_symbol
    && edge.asset_symbol.toUpperCase() === nativeSymbol.toUpperCase()
  ) {
    return {
      mode: 'native',
      chain,
      asset_symbol: nativeSymbol,
      canonical_asset_id: edge.canonical_asset_id,
    };
  }

  return null;
}

export function describeEdgeSelectiveTraceScope(
  edge: Pick<InvestigationEdge, 'tx_chain' | 'asset_symbol' | 'canonical_asset_id' | 'chain_asset_id'>,
  fallbackChain?: string,
): string {
  return deriveEdgeTraceAssetSelector(edge, fallbackChain)
    ? 'Continue using only this transaction hash and the concrete asset carried by this edge.'
    : 'Continue using only this transaction hash. This edge does not expose a safe chain-local asset identity, so the trace stays transaction-scoped only.';
}
