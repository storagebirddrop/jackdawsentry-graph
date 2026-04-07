/**
 * TypeScript types mirroring the ExpansionResponse v2 Pydantic contract
 * defined in src/trace_compiler/models.py.
 *
 * Keep in sync with the Python model — any field change in models.py must
 * be reflected here.
 */

// ---------------------------------------------------------------------------
// Node type discriminators
// ---------------------------------------------------------------------------

export type NodeType =
  | 'address'
  | 'entity'
  | 'service'
  | 'bridge_hop'
  | 'swap_event'
  | 'lightning_channel_open'
  | 'lightning_channel_close'
  | 'btc_sidechain_peg_in'
  | 'btc_sidechain_peg_out'
  | 'atomic_swap'
  | 'utxo'
  | 'solana_instruction'
  | 'cluster_summary';

export type EdgeType =
  | 'transfer'
  | 'bridge_hop'
  | 'bridge_source'
  | 'bridge_dest'
  | 'swap_edge'
  | 'swap_input'
  | 'swap_output'
  | 'entity_membership'
  | 'cluster_member'
  | 'service_deposit'
  | 'service_receipt'
  | 'annotation';

export type ActivityType =
  | 'bridge'
  | 'service_interaction'
  | 'dex_interaction'
  | 'mixer_interaction'
  | 'router_interaction'
  | 'cex_interaction'
  | 'lightning_channel_open'
  | 'lightning_channel_close'
  | 'btc_sidechain_peg_in'
  | 'btc_sidechain_peg_out'
  | 'atomic_swap';

// ---------------------------------------------------------------------------
// Node data payloads — each NodeType has a corresponding data shape
// ---------------------------------------------------------------------------

export interface AddressNodeData {
  address: string;
  chain: string;
  address_type?: string;
  entity_id?: string;
  entity_name?: string;
  entity_category?: string;
  risk_score?: number;
  is_sanctioned?: boolean;
  is_mixer?: boolean;
  is_coinjoin_halt?: boolean;
  label?: string;
  fiat_value_usd?: number;
}

export interface EntityNodeData {
  entity_id: string;
  name: string;
  category: string;
  address_count: number;
  risk_score?: number;
  jurisdiction?: string;
}

export interface ServiceNodeData {
  protocol_id: string;
  service_type: string;
  display_name: string;
  chain?: string;
}

export interface BridgeHopData {
  hop_id: string;
  protocol_id: string;
  mechanism: string;
  source_chain: string;
  destination_chain?: string;
  destination_address?: string;
  source_asset?: string;
  destination_asset?: string;
  source_amount?: number;
  destination_amount?: number | null;
  destination_tx_hash?: string;
  correlation_confidence: number;
  status: 'pending' | 'completed' | 'failed';
  time_delta_seconds?: number;
  is_same_asset?: boolean;
}

export interface SwapEventData {
  swap_id: string;
  protocol_id: string;
  chain: string;
  input_asset: string;
  output_asset: string;
  input_amount?: number;
  output_amount?: number;
  exchange_rate?: number;
  route_summary?: string;
  tx_hash?: string;
  timestamp?: string;
}

export interface LightningChannelOpenData {
  channel_id: string;
  funding_tx_hash: string;
  funding_vout?: number;
  short_channel_id?: string;
  capacity_btc: number;
  local_pubkey?: string;
  remote_pubkey?: string;
  local_alias?: string;
  remote_alias?: string;
  is_private?: boolean;
  status?: 'pending' | 'open' | 'closed';
}

export interface LightningChannelCloseData {
  channel_id: string;
  close_tx_hash: string;
  close_type?: 'cooperative' | 'force' | 'breach' | 'unknown';
  settled_btc?: number;
  local_pubkey?: string;
  remote_pubkey?: string;
  local_alias?: string;
  remote_alias?: string;
  status?: 'closing' | 'closed';
}

export interface BtcSidechainPegData {
  sidechain: 'liquid' | 'rootstock' | 'stacks' | string;
  bitcoin_tx_hash?: string;
  sidechain_tx_hash?: string;
  peg_address_or_contract?: string;
  asset_in: string;
  asset_out: string;
  amount_btc?: number;
  amount_sidechain?: number;
  mechanism?: 'federated' | 'contract' | 'bridge' | string;
  confidence?: number;
  status?: 'observed' | 'correlated' | 'settled' | 'failed';
}

export interface AtomicSwapData {
  swap_id: string;
  protocol_id?: string;
  source_chain: string;
  destination_chain: string;
  source_tx_hash?: string;
  destination_tx_hash?: string;
  hashlock?: string;
  timelock?: number;
  source_asset: string;
  destination_asset: string;
  source_amount?: number;
  destination_amount?: number;
  state?: 'locked' | 'redeemed' | 'refunded' | 'partial' | 'failed';
}

export interface UTXONodeData {
  address: string;
  script_type: string;
  address_type?: string;
  is_coinjoin_halt?: boolean;
  is_probable_change?: boolean;
}

export interface SolanaInstructionData {
  program_id: string;
  program_name?: string;
  instruction_type?: string;
  decode_status: 'full' | 'partial' | 'unknown';
  decoded_args?: Record<string, unknown>;
}

export interface ClusterSummaryData {
  total_nodes: number;
  dominant_type: NodeType;
  max_risk_score?: number;
  representative_address?: string;
}

export interface ActivitySummary {
  activity_type: ActivityType;
  title: string;
  protocol_id?: string;
  protocol_type?: string;
  tx_hash?: string;
  tx_chain?: string;
  timestamp?: string;
  direction?: string;
  status?: string;
  contract_address?: string;
  source_chain?: string;
  destination_chain?: string;
  source_tx_hash?: string;
  destination_tx_hash?: string;
  destination_address?: string;
  order_id?: string;
  asset_symbol?: string;
  canonical_asset_id?: string;
  chain_asset_id?: string;
  value_native?: number;
  value_fiat?: number;
  source_asset?: string;
  destination_asset?: string;
  source_amount?: number;
  destination_amount?: number;
  route_summary?: string;
  method_name?: string;
}

export type NodeData =
  | AddressNodeData
  | EntityNodeData
  | ServiceNodeData
  | BridgeHopData
  | SwapEventData
  | LightningChannelOpenData
  | LightningChannelCloseData
  | BtcSidechainPegData
  | AtomicSwapData
  | UTXONodeData
  | SolanaInstructionData
  | ClusterSummaryData;

// ---------------------------------------------------------------------------
// Frontend-only layout state
// ---------------------------------------------------------------------------

export type NodePlacementSource =
  | 'session_seed'
  | 'local_expansion'
  | 'elk_refinement'
  | 'manual_drag'
  | 'snapshot_restore';

export interface NodeLayoutMetadata {
  layoutLocked: boolean;
  userPlaced: boolean;
  placementSource: NodePlacementSource;
  anchorNodeId?: string;
  lastLayoutToken?: string;
}

// ---------------------------------------------------------------------------
// Investigation node and edge
// ---------------------------------------------------------------------------

export interface InvestigationNode {
  node_id: string;
  node_type: NodeType;
  node_data?: NodeData;
  chain?: string;
  display_label?: string;
  display_sublabel?: string;
  entity_name?: string;
  entity_type?: string;
  entity_category?: string;
  risk_score?: number;
  sanctioned?: boolean;
  balance_fiat?: number;
  address_data?: AddressNodeData;  // shorthand alias used by some compilers
  entity_data?: EntityNodeData;
  service_data?: ServiceNodeData;
  bridge_hop_data?: BridgeHopData;
  swap_event_data?: SwapEventData;
  lightning_channel_open_data?: LightningChannelOpenData;
  lightning_channel_close_data?: LightningChannelCloseData;
  btc_sidechain_peg_data?: BtcSidechainPegData;
  atomic_swap_data?: AtomicSwapData;
  utxo_data?: UTXONodeData;
  instruction_data?: SolanaInstructionData;
  cluster_summary?: ClusterSummaryData;
  branch_id: string;
  path_id: string;
  lineage_id: string;
  depth: number;
  expandable_directions: Array<'next' | 'prev' | 'neighbors'>;
  is_expanded?: boolean;
  is_pinned?: boolean;
  is_hidden?: boolean;
  is_highlighted?: boolean;
  is_seed?: boolean;
  activity_summary?: ActivitySummary;
  /** Branch color index (0-7) assigned from branch_id hash */
  branch_color_index?: number;
}

const EVM_CHAINS = new Set([
  'ethereum',
  'bsc',
  'polygon',
  'arbitrum',
  'base',
  'avalanche',
  'optimism',
  'starknet',
  'injective',
]);

function canonicalizeNodeId(nodeId: string): string {
  const parts = nodeId.split(':');
  if (parts.length < 3) {
    return nodeId;
  }

  const chain = parts[0] ?? '';
  const nodeType = parts[1] ?? '';
  const identifier = parts.slice(2).join(':');
  if (nodeType === 'address' && EVM_CHAINS.has(chain) && identifier.startsWith('0x')) {
    const lowered = identifier.toLowerCase();
    return lowered === identifier ? nodeId : `${chain}:${nodeType}:${lowered}`;
  }
  return nodeId;
}

export function getInvestigationNodeData(node: InvestigationNode): NodeData | undefined {
  switch (node.node_type) {
    case 'address':
      return node.address_data ?? node.node_data;
    case 'entity':
      return node.entity_data ?? node.node_data;
    case 'service':
      return node.service_data ?? node.entity_data ?? node.node_data;
    case 'bridge_hop':
      return node.bridge_hop_data ?? node.node_data;
    case 'swap_event':
      return node.swap_event_data ?? node.node_data;
    case 'lightning_channel_open':
      return node.lightning_channel_open_data ?? node.node_data;
    case 'lightning_channel_close':
      return node.lightning_channel_close_data ?? node.node_data;
    case 'btc_sidechain_peg_in':
    case 'btc_sidechain_peg_out':
      return node.btc_sidechain_peg_data ?? node.node_data;
    case 'atomic_swap':
      return node.atomic_swap_data ?? node.node_data;
    case 'utxo':
      return node.utxo_data ?? node.node_data;
    case 'solana_instruction':
      return node.instruction_data ?? node.node_data;
    case 'cluster_summary':
      return node.cluster_summary ?? node.node_data;
    default:
      return node.node_data;
  }
}

export function normalizeInvestigationNode(node: InvestigationNode): InvestigationNode {
  const nodeData = getInvestigationNodeData(node);
  const canonicalNodeId = canonicalizeNodeId(node.node_id);

  if ((!nodeData || node.node_data === nodeData) && canonicalNodeId === node.node_id) {
    return node;
  }

  const normalizedNode: InvestigationNode = {
    ...node,
    node_id: canonicalNodeId,
    ...(nodeData ? { node_data: nodeData } : {}),
  };

  if (
    normalizedNode.node_type === 'address'
    && normalizedNode.address_data
    && EVM_CHAINS.has(normalizedNode.chain ?? '')
    && normalizedNode.address_data.address.startsWith('0x')
  ) {
    const normalizedAddress = normalizedNode.address_data.address.toLowerCase();
    const normalizedAddressData = {
      ...normalizedNode.address_data,
      address: normalizedAddress,
    };
    return {
      ...normalizedNode,
      address_data: normalizedAddressData,
      node_data: normalizedAddressData,
    };
  }

  return normalizedNode;
}

export interface InvestigationEdge {
  edge_id: string;
  edge_type: EdgeType;
  source_node_id: string;
  target_node_id: string;
  path_id?: string;
  direction: 'forward' | 'backward' | 'lateral';
  asset_symbol?: string;
  canonical_asset_id?: string;
  chain_asset_id?: string;
  value_native?: number;
  fiat_value_usd?: number;
  value_fiat?: number;
  tx_hash?: string;
  tx_chain?: string;
  timestamp?: string;
  is_suspected_change?: boolean;
  activity_summary?: ActivitySummary;
  branch_id: string;
  /** Same branch_color_index as the source node */
  branch_color_index?: number;
}

export function normalizeInvestigationEdge(edge: InvestigationEdge): InvestigationEdge {
  const sourceNodeId = canonicalizeNodeId(edge.source_node_id);
  const targetNodeId = canonicalizeNodeId(edge.target_node_id);

  if (sourceNodeId === edge.source_node_id && targetNodeId === edge.target_node_id) {
    return edge;
  }

  return {
    ...edge,
    source_node_id: sourceNodeId,
    target_node_id: targetNodeId,
  };
}

// ---------------------------------------------------------------------------
// ExpansionResponse v2
// ---------------------------------------------------------------------------

export interface LayoutHints {
  suggested_layout: 'layered' | 'force_directed' | 'force' | 'hierarchical';
  direction?: 'LR' | 'TB';
  spacing?: number;
  anchor_node_ids?: string[];
  new_branch_root_id?: string | null;
  collapse_candidates?: string[];
}

export interface ChainContext {
  primary_chain: string;
  chains_present: string[];
}

export interface PaginationMeta {
  has_more: boolean;
  next_cursor?: string;
  total_available?: number;
}

export interface AssetContext {
  assets_present: string[];
  canonical_asset_ids: string[];
  total_value_fiat?: number;
}

export interface AssetSelector {
  mode: 'all' | 'native' | 'asset';
  chain: string;
  chain_asset_id?: string;
  asset_symbol?: string;
  canonical_asset_id?: string;
}

export interface AssetOption extends AssetSelector {
  display_label: string;
}

export interface ExpansionEmptyState {
  reason: string;
  message: string;
  chain: string;
  address?: string;
  operation_type?: 'expand_next' | 'expand_prev' | 'expand_neighbors';
  live_lookup_supported?: boolean;
  observed_on_chain?: boolean;
  known_tx_count?: number;
}

export interface ExpansionResponseV2 {
  session_id: string;
  branch_id: string;
  parent_branch_id?: string | null;
  expansion_depth?: number;
  operation_id: string;
  operation_type: 'expand_next' | 'expand_prev' | 'expand_neighbors' | 'create_session';
  seed_node_id?: string;
  seed_lineage_id?: string | null;
  nodes: InvestigationNode[];
  edges: InvestigationEdge[];
  added_nodes?: InvestigationNode[];
  added_edges?: InvestigationEdge[];
  updated_nodes?: InvestigationNode[];
  removed_node_ids?: string[];
  layout_hints: LayoutHints;
  chain_context: ChainContext;
  pagination?: PaginationMeta;
  asset_context?: AssetContext;
  empty_state?: ExpansionEmptyState;
  integrity_warning?: string;
  ingest_pending?: boolean;
  timestamp?: string;
}

// ---------------------------------------------------------------------------
// Session API shapes
// ---------------------------------------------------------------------------

export interface SessionCreateRequest {
  seed_address: string;
  seed_chain: string;
  case_id?: string;
}

export interface SessionCreateResponse {
  session_id: string;
  root_node: InvestigationNode;
  created_at?: string;
}

export interface AssetCatalogItem {
  asset_key: string;
  symbol: string;
  display_name?: string;
  canonical_asset_id?: string;
  canonical_symbol?: string;
  identity_status: 'verified' | 'heuristic' | 'unknown';
  variant_kind: 'native' | 'canonical' | 'wrapped' | 'bridged' | 'unknown';
  blockchains: string[];
  token_standards: string[];
  observed_transfer_count: number;
  last_seen_at?: string;
  sample_asset_address?: string;
  is_native: boolean;
}

export interface AssetCatalogResponse {
  session_id: string;
  seed_chain: string;
  chains_present: string[];
  items: AssetCatalogItem[];
  generated_at?: string;
}

export interface WorkspacePosition {
  x: number;
  y: number;
}

export interface WorkspaceBranchSnapshot {
  branchId: string;
  color: string;
  seedNodeId: string;
  minDepth: number;
  maxDepth: number;
  nodeCount: number;
}

export interface WorkspacePreferencesSnapshot {
  selectedAssets: string[];
  pinnedAssetKeys: string[];
  assetCatalogScope: 'session' | 'visible';
}

export interface WorkspaceSnapshotV1 {
  schema_version?: number;
  revision: number;
  sessionId: string;
  nodes: InvestigationNode[];
  edges: InvestigationEdge[];
  positions: Record<string, WorkspacePosition>;
  branches?: WorkspaceBranchSnapshot[] | null;
  workspacePreferences?: WorkspacePreferencesSnapshot | null;
}

export interface SessionSnapshotResponse {
  snapshot_id: string;
  saved_at?: string;
  revision: number;
}

export interface RecentSessionSummary {
  session_id: string;
  seed_address?: string | null;
  seed_chain?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  snapshot_saved_at?: string | null;
}

export interface RecentSessionsResponse {
  items: RecentSessionSummary[];
}

export interface InvestigationSessionResponse {
  session_id: string;
  seed_address?: string | null;
  seed_chain?: string | null;
  case_id?: string | null;
  snapshot?: unknown;
  workspace: WorkspaceSnapshotV1;
  restore_state: 'full' | 'legacy_bootstrap';
  nodes: InvestigationNode[];
  edges: InvestigationEdge[];
  branch_map: Record<string, WorkspaceBranchSnapshot>;
  created_at?: string | null;
  updated_at?: string | null;
  snapshot_saved_at?: string | null;
}

export interface ExpandRequest {
  operation_type: 'expand_next' | 'expand_prev' | 'expand_neighbors';
  seed_node_id: string;
  seed_lineage_id?: string;
  options?: {
    depth?: number;
    asset_filter?: string[];
    asset_selector?: AssetSelector;
    chain_filter?: string[];
    tx_hashes?: string[];
    min_value_fiat?: number;
    max_results?: number;
    include_services?: boolean;
    follow_bridges?: boolean;
    continuation_token?: string;
    page_size?: number;
    time_from?: string;
    time_to?: string;
  };
}

export interface AssetOptionsRequest {
  seed_node_id: string;
  seed_lineage_id?: string;
}

export interface AssetOptionsResponse {
  session_id: string;
  seed_node_id: string;
  seed_lineage_id?: string | null;
  options: AssetOption[];
}

export interface BridgeHopStatusResponse {
  hop_id: string;
  status: 'pending' | 'completed' | 'failed' | 'expired';
  destination_tx_hash?: string;
  destination_chain?: string;
  destination_address?: string;
  correlation_confidence?: number;
  updated_at?: string;
}

export interface IngestStatusResponse {
  address: string;
  blockchain: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'not_found';
  queued_at?: string;
  started_at?: string;
  completed_at?: string;
  tx_count?: number;
  error?: string;
}

export interface TxResolveResponse {
  found: boolean;
  tx_hash: string;
  blockchain: string;
  from_address?: string;
  to_address?: string;
  value_native?: number;
  asset_symbol?: string;
  timestamp?: string;
  block_number?: number;
  status?: string;
}
