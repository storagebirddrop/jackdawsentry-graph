import type { AssetOption, AssetSelector, InvestigationEdge } from '../types/graph';

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

export function assetOptionKey(
  option: Pick<AssetSelector, 'mode' | 'chain' | 'chain_asset_id' | 'asset_symbol' | 'canonical_asset_id'>,
): string {
  if (option.mode === 'all') {
    return `all:${option.chain}`;
  }
  if (option.mode === 'native') {
    return `native:${option.chain}`;
  }
  return [
    'asset',
    option.chain,
    option.chain_asset_id ?? '',
    option.asset_symbol ?? '',
    option.canonical_asset_id ?? '',
  ].join(':');
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
