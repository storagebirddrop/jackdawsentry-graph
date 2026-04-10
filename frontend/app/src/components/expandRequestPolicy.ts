import type {
  AddressNodeData,
  AssetOption,
  AssetSelector,
  ExpandRequest,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';
import {
  deriveEdgeTraceAssetSelector,
  getStoredNodeAssetSelectors,
} from './assetExpansionPolicy';

export interface ExpandInvocation {
  node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>;
  operation: ExpandRequest['operation_type'];
  txHashes?: string[];
  /** Multi-asset selectors for this expansion. Empty array means "all assets". */
  assetSelectors?: AssetSelector[];
}

function toRequestAssetSelector(selector: AssetSelector): AssetSelector {
  return {
    mode: selector.mode,
    chain: selector.chain,
    chain_asset_id: selector.chain_asset_id,
    asset_symbol: selector.asset_symbol,
    canonical_asset_id: selector.canonical_asset_id,
  };
}

type TraceEndpointNode = Pick<
  InvestigationNode,
  'node_id' | 'lineage_id' | 'node_type' | 'chain' | 'address_data'
>;

type TraceableEdge = Pick<
  InvestigationEdge,
  'tx_hash' | 'tx_chain' | 'asset_symbol' | 'canonical_asset_id' | 'chain_asset_id'
>;

export function buildExpandRequest(invocation: ExpandInvocation): ExpandRequest {
  const requestOptions: NonNullable<ExpandRequest['options']> = {};

  if (invocation.txHashes && invocation.txHashes.length > 0) {
    requestOptions.tx_hashes = invocation.txHashes;
  }

  const selectors = invocation.assetSelectors ?? [];
  // Filter out any "all" mode entries; empty list means no filter on the backend.
  const specificSelectors = selectors.filter((s) => s.mode !== 'all');
  if (specificSelectors.length > 0) {
    requestOptions.asset_selectors = specificSelectors.map(toRequestAssetSelector);
  }

  return {
    seed_node_id: invocation.node.node_id,
    seed_lineage_id: invocation.node.lineage_id,
    operation_type: invocation.operation,
    options: Object.keys(requestOptions).length > 0 ? requestOptions : undefined,
  };
}

export function createInspectorExpandInvocation(
  node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>,
  operation: ExpandRequest['operation_type'],
  assetSelectors?: AssetSelector[],
): ExpandInvocation {
  return {
    node,
    operation,
    assetSelectors,
  };
}

export function createQuickExpandInvocation(
  node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>,
  operation: Extract<ExpandRequest['operation_type'], 'expand_prev' | 'expand_next'>,
  selectedKeysByNodeId: ReadonlyMap<string, readonly string[]>,
  optionsByNodeId: ReadonlyMap<string, AssetOption[]>,
): ExpandInvocation {
  return {
    node,
    operation,
    assetSelectors: getStoredNodeAssetSelectors(node.node_id, selectedKeysByNodeId, optionsByNodeId),
  };
}

export function createEdgeTraceInvocation(
  edge: TraceableEdge,
  endpoint: TraceEndpointNode | null | undefined,
  direction: 'forward' | 'backward',
): ExpandInvocation | null {
  if (!edge.tx_hash || !endpoint || endpoint.node_type !== 'address') {
    return null;
  }

  const endpointAddressData = endpoint.address_data as AddressNodeData | undefined;
  const edgeSelector = deriveEdgeTraceAssetSelector(
    edge,
    endpoint.chain ?? endpointAddressData?.chain,
  );

  return {
    node: endpoint,
    operation: direction === 'forward' ? 'expand_next' : 'expand_prev',
    txHashes: [edge.tx_hash],
    // Edge selective trace stays single-asset: derive from the edge itself,
    // not from the user's node-level multi-selection.
    assetSelectors: edgeSelector ? [edgeSelector] : [],
  };
}
