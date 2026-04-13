import type {
  AssetOption,
  AssetOptionsResponse,
  AssetSelector,
  ExpansionResponseV2,
  InvestigationEdge,
  InvestigationNode,
  InvestigationSessionResponse,
  RecentSessionSummary,
  WorkspaceBranchSnapshot,
  WorkspaceSnapshotV1,
} from '../../src/types/graph';

export const SESSION_ID = 'sess-browser-e2e';
export const BRANCH_ID = 'branch-1';
export const ROOT_NODE_ID = 'ethereum:address:0xaaa';

export const ROOT_SELECTOR: AssetSelector = {
  mode: 'asset',
  chain: 'ethereum',
  chain_asset_id: '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
  asset_symbol: 'USDC',
  canonical_asset_id: 'usdc',
};

export const SECONDARY_SELECTOR: AssetSelector = {
  mode: 'asset',
  chain: 'ethereum',
  chain_asset_id: '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
  asset_symbol: 'WETH',
  canonical_asset_id: 'weth',
};

export const ROOT_NODE: InvestigationNode = {
  node_id: ROOT_NODE_ID,
  node_type: 'address',
  branch_id: BRANCH_ID,
  path_id: 'path-root',
  lineage_id: 'lineage-root',
  depth: 0,
  chain: 'ethereum',
  display_label: '0xaaa',
  expandable_directions: ['next', 'prev', 'neighbors'],
  address_data: {
    address: '0xaaa',
    chain: 'ethereum',
  },
  is_seed: true,
};

export const PREVIEW_NODE_A: InvestigationNode = {
  node_id: 'ethereum:address:0xpreviewa',
  node_type: 'address',
  branch_id: BRANCH_ID,
  path_id: 'path-preview-a',
  lineage_id: 'lineage-preview-a',
  depth: 1,
  chain: 'ethereum',
  display_label: '0xpreviewa',
  expandable_directions: ['next'],
  address_data: {
    address: '0xpreviewa',
    chain: 'ethereum',
  },
};

export const PREVIEW_NODE_B: InvestigationNode = {
  node_id: 'ethereum:address:0xpreviewb',
  node_type: 'address',
  branch_id: BRANCH_ID,
  path_id: 'path-preview-b',
  lineage_id: 'lineage-preview-b',
  depth: 1,
  chain: 'ethereum',
  display_label: '0xpreviewb',
  expandable_directions: ['next'],
  address_data: {
    address: '0xpreviewb',
    chain: 'ethereum',
  },
};

export const PREVIEW_EDGE_A: InvestigationEdge = {
  edge_id: 'edge-preview-a',
  edge_type: 'transfer',
  source_node_id: ROOT_NODE_ID,
  target_node_id: PREVIEW_NODE_A.node_id,
  direction: 'forward',
  branch_id: BRANCH_ID,
  tx_hash: '0xtxa',
  tx_chain: 'ethereum',
  asset_symbol: 'USDC',
  chain_asset_id: ROOT_SELECTOR.chain_asset_id,
  value_native: 1250,
  value_fiat: 1250,
  timestamp: '2026-04-13T10:10:00Z',
};

export const PREVIEW_EDGE_B: InvestigationEdge = {
  edge_id: 'edge-preview-b',
  edge_type: 'transfer',
  source_node_id: ROOT_NODE_ID,
  target_node_id: PREVIEW_NODE_B.node_id,
  direction: 'forward',
  branch_id: BRANCH_ID,
  tx_hash: '0xtxb',
  tx_chain: 'ethereum',
  asset_symbol: 'WETH',
  chain_asset_id: SECONDARY_SELECTOR.chain_asset_id,
  value_native: 2.5,
  value_fiat: 4500,
  timestamp: '2026-04-13T10:12:00Z',
};

export const ASSET_OPTIONS: AssetOption[] = [
  {
    ...ROOT_SELECTOR,
    display_label: 'USDC · Ethereum',
  },
  {
    ...SECONDARY_SELECTOR,
    display_label: 'WETH · Ethereum',
  },
  {
    mode: 'native',
    chain: 'ethereum',
    asset_symbol: 'ETH',
    canonical_asset_id: 'eth',
    display_label: 'ETH · Ethereum',
  },
];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function defaultBranches(): WorkspaceBranchSnapshot[] {
  return [
    {
      branchId: BRANCH_ID,
      color: '#3b82f6',
      seedNodeId: ROOT_NODE_ID,
      minDepth: 0,
      maxDepth: 0,
      nodeCount: 1,
    },
  ];
}

export function makeRestoreCandidate(): RecentSessionSummary {
  return {
    session_id: SESSION_ID,
    seed_address: '0xaaa',
    seed_chain: 'ethereum',
    snapshot_saved_at: '2026-04-13T10:00:00Z',
    created_at: '2026-04-13T09:55:00Z',
    updated_at: '2026-04-13T10:00:00Z',
  };
}

export function makeWorkspaceSnapshot(options?: {
  revision?: number;
  nodes?: InvestigationNode[];
  edges?: InvestigationEdge[];
  positions?: Record<string, { x: number; y: number }>;
  nodeAssetScopes?: Record<string, AssetSelector[]> | null;
}): WorkspaceSnapshotV1 {
  const nodes = options?.nodes ?? [ROOT_NODE];
  const edges = options?.edges ?? [];

  return {
    schema_version: 1,
    revision: options?.revision ?? 8,
    sessionId: SESSION_ID,
    nodes: clone(nodes),
    edges: clone(edges),
    positions: options?.positions ?? {
      [ROOT_NODE_ID]: { x: 24, y: 32 },
    },
    branches: defaultBranches(),
    nodeAssetScopes: options?.nodeAssetScopes ?? {
      [ROOT_NODE_ID]: [ROOT_SELECTOR],
    },
    workspacePreferences: {
      selectedAssets: [],
      pinnedAssetKeys: [],
      assetCatalogScope: 'session',
    },
  };
}

export function makeAssetOptionsResponse(): AssetOptionsResponse {
  return {
    session_id: SESSION_ID,
    seed_node_id: ROOT_NODE_ID,
    seed_lineage_id: ROOT_NODE.lineage_id,
    options: clone(ASSET_OPTIONS),
  };
}

export function makePreviewResponse(): ExpansionResponseV2 {
  return {
    session_id: SESSION_ID,
    branch_id: BRANCH_ID,
    expansion_depth: 1,
    operation_id: 'op-preview-next',
    operation_type: 'expand_next',
    seed_node_id: ROOT_NODE_ID,
    seed_lineage_id: ROOT_NODE.lineage_id,
    added_nodes: clone([PREVIEW_NODE_A, PREVIEW_NODE_B]),
    added_edges: clone([PREVIEW_EDGE_A, PREVIEW_EDGE_B]),
    updated_nodes: [],
    removed_node_ids: [],
    has_more: false,
    continuation_token: null,
    layout_hints: {
      suggested_layout: 'layered',
    },
    chain_context: {
      primary_chain: 'ethereum',
      chains_present: ['ethereum'],
    },
    asset_context: {
      assets_present: ['USDC', 'WETH'],
      canonical_asset_ids: ['usdc', 'weth'],
    },
    pagination: {
      page_size: 25,
      max_results: 100,
      has_more: false,
      next_token: null,
    },
    data_sources: ['event_store'],
  };
}

export function makeSessionResponse(
  workspace: WorkspaceSnapshotV1,
  options?: {
    snapshotSavedAt?: string | null;
  },
): InvestigationSessionResponse {
  const branches = workspace.branches ?? [];
  return {
    session_id: workspace.sessionId,
    seed_address: '0xaaa',
    seed_chain: 'ethereum',
    workspace: clone(workspace),
    restore_state: 'full',
    nodes: clone(workspace.nodes),
    edges: clone(workspace.edges),
    branch_map: Object.fromEntries(branches.map((branch) => [branch.branchId, branch])),
    created_at: '2026-04-13T09:55:00Z',
    updated_at: options?.snapshotSavedAt ?? '2026-04-13T10:00:00Z',
    snapshot_saved_at: options?.snapshotSavedAt ?? '2026-04-13T10:00:00Z',
  };
}
