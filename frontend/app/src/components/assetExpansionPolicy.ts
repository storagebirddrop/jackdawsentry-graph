import type { AssetSelector, InvestigationEdge } from '../types/graph';

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

export function getStoredNodeAssetSelector(
  nodeId: string,
  selectorsByNodeId: ReadonlyMap<string, AssetSelector>,
): AssetSelector | null {
  const selector = selectorsByNodeId.get(nodeId) ?? null;
  if (!selector || selector.mode === 'all') {
    return null;
  }
  return selector;
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
