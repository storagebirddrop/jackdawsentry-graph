import type {
  AddressNodeData,
  AssetSelector,
  ExpandRequest,
  InvestigationEdge,
  InvestigationNode,
} from '../types/graph';
import {
  deriveEdgeTraceAssetSelector,
  getStoredNodeAssetSelector,
} from './assetExpansionPolicy';

export interface ExpandInvocation {
  node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>;
  operation: ExpandRequest['operation_type'];
  txHashes?: string[];
  assetSelector?: AssetSelector | null;
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
  if (invocation.assetSelector && invocation.assetSelector.mode !== 'all') {
    requestOptions.asset_selector = invocation.assetSelector;
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
  assetSelector?: AssetSelector | null,
): ExpandInvocation {
  return {
    node,
    operation,
    assetSelector,
  };
}

export function createQuickExpandInvocation(
  node: Pick<InvestigationNode, 'node_id' | 'lineage_id'>,
  operation: Extract<ExpandRequest['operation_type'], 'expand_prev' | 'expand_next'>,
  selectorsByNodeId: ReadonlyMap<string, AssetSelector>,
): ExpandInvocation {
  return {
    node,
    operation,
    assetSelector: getStoredNodeAssetSelector(node.node_id, selectorsByNodeId),
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

  return {
    node: endpoint,
    operation: direction === 'forward' ? 'expand_next' : 'expand_prev',
    txHashes: [edge.tx_hash],
    assetSelector: deriveEdgeTraceAssetSelector(
      edge,
      endpoint.chain ?? endpointAddressData?.chain,
    ),
  };
}
