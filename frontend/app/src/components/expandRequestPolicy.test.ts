import { describe, expect, it } from 'vitest';

import type { AssetSelector, InvestigationEdge, InvestigationNode } from '../types/graph';
import {
  buildExpandRequest,
  createEdgeTraceInvocation,
  createInspectorExpandInvocation,
  createQuickExpandInvocation,
} from './expandRequestPolicy';

function makeAddressNode(
  overrides: Partial<InvestigationNode> = {},
): InvestigationNode {
  return {
    node_id: 'ethereum:address:0xabc',
    branch_id: 'branch-1',
    path_id: 'path-1',
    lineage_id: 'lineage-1',
    node_type: 'address',
    depth: 0,
    chain: 'ethereum',
    expandable_directions: ['prev', 'next', 'neighbors'],
    address_data: {
      address: '0xabc',
      chain: 'ethereum',
    },
    ...overrides,
  };
}

function makeEdge(overrides: Partial<InvestigationEdge> = {}): InvestigationEdge {
  return {
    edge_id: 'edge-1',
    edge_type: 'transfer',
    source_node_id: 'ethereum:address:0xsource',
    target_node_id: 'ethereum:address:0xtarget',
    direction: 'forward',
    branch_id: 'branch-1',
    tx_hash: '0xtx',
    tx_chain: 'ethereum',
    ...overrides,
  };
}

describe('expand request policy', () => {
  it('builds the inspector expand request with the selected asset scope', () => {
    const node = makeAddressNode();
    const selector: AssetSelector = {
      mode: 'asset',
      chain: 'ethereum',
      chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
      asset_symbol: 'USDC',
    };

    expect(
      buildExpandRequest(
        createInspectorExpandInvocation(node, 'expand_neighbors', selector),
      ),
    ).toEqual({
      seed_node_id: node.node_id,
      seed_lineage_id: node.lineage_id,
      operation_type: 'expand_neighbors',
      options: {
        asset_selector: selector,
      },
    });
  });

  it('builds the quick expand request from the stored per-node asset scope', () => {
    const node = makeAddressNode();
    const selector: AssetSelector = {
      mode: 'asset',
      chain: 'ethereum',
      chain_asset_id: '0xa0b8',
      asset_symbol: 'USDC',
    };

    expect(
      buildExpandRequest(
        createQuickExpandInvocation(
          node,
          'expand_prev',
          new Map([[node.node_id, selector]]),
        ),
      ),
    ).toEqual({
      seed_node_id: node.node_id,
      seed_lineage_id: node.lineage_id,
      operation_type: 'expand_prev',
      options: {
        asset_selector: selector,
      },
    });
  });

  it('builds an edge selective trace request with tx hash and safe asset scope', () => {
    const endpoint = makeAddressNode({
      node_id: 'ethereum:address:0xtarget',
      address_data: {
        address: '0xtarget',
        chain: 'ethereum',
      },
    });
    const edge = makeEdge({
      asset_symbol: 'USDC',
      chain_asset_id: '0xa0b8',
      canonical_asset_id: 'usdc',
    });

    const invocation = createEdgeTraceInvocation(edge, endpoint, 'forward');

    expect(invocation).not.toBeNull();
    expect(buildExpandRequest(invocation!)).toEqual({
      seed_node_id: endpoint.node_id,
      seed_lineage_id: endpoint.lineage_id,
      operation_type: 'expand_next',
      options: {
        tx_hashes: ['0xtx'],
        asset_selector: {
          mode: 'asset',
          chain: 'ethereum',
          chain_asset_id: '0xa0b8',
          asset_symbol: 'USDC',
          canonical_asset_id: 'usdc',
        },
      },
    });
  });

  it('keeps edge selective trace transaction-scoped when the edge lacks safe asset identity', () => {
    const endpoint = makeAddressNode({
      node_id: 'ethereum:address:0xsource',
      address_data: {
        address: '0xsource',
        chain: 'ethereum',
      },
    });
    const edge = makeEdge({
      asset_symbol: 'USDT',
      chain_asset_id: undefined,
      canonical_asset_id: 'tether',
    });

    const invocation = createEdgeTraceInvocation(edge, endpoint, 'backward');

    expect(invocation).not.toBeNull();
    expect(buildExpandRequest(invocation!)).toEqual({
      seed_node_id: endpoint.node_id,
      seed_lineage_id: endpoint.lineage_id,
      operation_type: 'expand_prev',
      options: {
        tx_hashes: ['0xtx'],
      },
    });
  });
});
