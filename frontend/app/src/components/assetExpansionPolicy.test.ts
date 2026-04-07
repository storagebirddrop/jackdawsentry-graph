import { describe, expect, it } from 'vitest';

import type { AssetSelector, InvestigationEdge } from '../types/graph';
import {
  describeEdgeSelectiveTraceScope,
  deriveEdgeTraceAssetSelector,
  getStoredNodeAssetSelector,
} from './assetExpansionPolicy';

function makeEdge(overrides: Partial<InvestigationEdge> = {}): InvestigationEdge {
  return {
    edge_id: 'edge-1',
    edge_type: 'transfer',
    source_node_id: 'ethereum:address:0xsource',
    target_node_id: 'ethereum:address:0xtarget',
    direction: 'forward',
    branch_id: 'branch-1',
    ...overrides,
  };
}

describe('getStoredNodeAssetSelector', () => {
  it('returns null when no selector was stored', () => {
    expect(getStoredNodeAssetSelector('node-1', new Map())).toBeNull();
  });

  it('treats the all-assets option as unscoped', () => {
    const selectors = new Map<string, AssetSelector>([
      ['node-1', { mode: 'all', chain: 'ethereum' }],
    ]);

    expect(getStoredNodeAssetSelector('node-1', selectors)).toBeNull();
  });

  it('returns the stored asset-specific selector for quick expand reuse', () => {
    const selector: AssetSelector = {
      mode: 'asset',
      chain: 'ethereum',
      chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
      asset_symbol: 'USDC',
    };
    const selectors = new Map<string, AssetSelector>([['node-1', selector]]);

    expect(getStoredNodeAssetSelector('node-1', selectors)).toEqual(selector);
  });
});

describe('deriveEdgeTraceAssetSelector', () => {
  it('returns an asset selector when the edge carries a chain-local asset id', () => {
    expect(
      deriveEdgeTraceAssetSelector(
        makeEdge({
          tx_chain: 'ethereum',
          asset_symbol: 'USDC',
          chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        }),
      ),
    ).toEqual({
      mode: 'asset',
      chain: 'ethereum',
      chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
      asset_symbol: 'USDC',
      canonical_asset_id: undefined,
    });
  });

  it('returns a native selector when the edge is native-value scoped', () => {
    expect(
      deriveEdgeTraceAssetSelector(
        makeEdge({
          tx_chain: 'solana',
          asset_symbol: 'SOL',
        }),
      ),
    ).toEqual({
      mode: 'native',
      chain: 'solana',
      asset_symbol: 'SOL',
      canonical_asset_id: undefined,
    });
  });

  it('returns null for token-like edges that lack safe chain-local identity', () => {
    expect(
      deriveEdgeTraceAssetSelector(
        makeEdge({
          tx_chain: 'ethereum',
          asset_symbol: 'USDT',
        }),
      ),
    ).toBeNull();
  });
});

describe('describeEdgeSelectiveTraceScope', () => {
  it('describes scoped tracing when a safe asset scope can be derived', () => {
    const message = describeEdgeSelectiveTraceScope(
      makeEdge({
        tx_chain: 'tron',
        asset_symbol: 'USDT',
        chain_asset_id: 'TXYZopYRdj2D9XRtbG411XZZ3kM5VkAeBf',
      }),
    );

    expect(message).toContain('concrete asset');
  });

  it('describes tx-hash-only tracing when the edge lacks safe asset identity', () => {
    const message = describeEdgeSelectiveTraceScope(
      makeEdge({
        tx_chain: 'ethereum',
        asset_symbol: 'USDC',
      }),
    );

    expect(message).toContain('transaction-scoped only');
  });
});
