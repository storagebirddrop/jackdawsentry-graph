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
  source_asset?: string;
  destination_asset?: string;
  source_amount?: number;
  destination_amount?: number | null;
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
  order_id?: string;
  asset_symbol?: string;
  canonical_asset_id?: string;
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
// Investigation node and edge
// ---------------------------------------------------------------------------

export interface InvestigationNode {
  node_id: string;
  node_type: NodeType;
  node_data: NodeData;
  chain?: string;
  display_label?: string;
  display_sublabel?: string;
  entity_name?: string;
  entity_category?: string;
  risk_score?: number;
  // Top-level sanctioned flag set by the enricher (mirrors address_data.is_sanctioned).
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
  is_seed?: boolean;
  activity_summary?: ActivitySummary;
  /** Branch color index (0-7) assigned from branch_id hash */
  branch_color_index?: number;
}

export interface InvestigationEdge {
  edge_id: string;
  edge_type: EdgeType;
  source_node_id: string;
  target_node_id: string;
  direction: 'forward' | 'backward';
  asset_symbol?: string;
  canonical_asset_id?: string;
  value_native?: number;
  fiat_value_usd?: number;
  tx_hash?: string;
  timestamp?: string;
  is_suspected_change?: boolean;
  activity_summary?: ActivitySummary;
  branch_id: string;
  /** Same branch_color_index as the source node */
  branch_color_index?: number;
}

// ---------------------------------------------------------------------------
// ExpansionResponse v2
// ---------------------------------------------------------------------------

export interface LayoutHints {
  suggested_layout: 'layered' | 'force_directed' | 'hierarchical';
  direction?: 'LR' | 'TB';
  spacing?: number;
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
}

export interface ExpansionResponseV2 {
  session_id: string;
  branch_id: string;
  operation_id: string;
  operation_type: 'expand_next' | 'expand_prev' | 'expand_neighbors' | 'create_session';
  nodes: InvestigationNode[];
  edges: InvestigationEdge[];
  added_nodes?: InvestigationNode[];
  added_edges?: InvestigationEdge[];
  layout_hints: LayoutHints;
  chain_context: ChainContext;
  pagination?: PaginationMeta;
  asset_context?: AssetContext;
  /** True when expansion was empty because historical data is not yet in the
   *  event store.  The frontend should poll /ingest/status and retry expand. */
  ingest_pending?: boolean;
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
}

export interface ExpandRequest {
  operation_type: 'expand_next' | 'expand_prev' | 'expand_neighbors';
  seed_node_id: string;
  seed_lineage_id?: string;
  options?: {
    depth?: number;
    asset_filter?: string[];
    chain_filter?: string[];
    min_value_fiat?: number;
    max_results?: number;
    include_services?: boolean;
    follow_bridges?: boolean;
    continuation_token?: string;
    page_size?: number;
  };
}

export interface BridgeHopStatusResponse {
  hop_id: string;
  status: 'pending' | 'completed' | 'failed' | 'expired';
  destination_tx_hash?: string;
  destination_chain?: string;
  destination_address?: string;
  correlation_confidence?: number;
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
