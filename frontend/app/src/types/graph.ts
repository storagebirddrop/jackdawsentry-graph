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
  | 'utxo'
  | 'solana_instruction'
  | 'cluster_summary';

export type EdgeType =
  | 'transfer'
  | 'bridge_hop'
  | 'swap_edge'
  | 'entity_membership'
  | 'annotation';

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
  correlation_confidence: number;
  status: 'pending' | 'completed' | 'failed';
  time_delta_seconds?: number;
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

export type NodeData =
  | AddressNodeData
  | EntityNodeData
  | ServiceNodeData
  | BridgeHopData
  | SwapEventData
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
  address_data?: AddressNodeData;  // shorthand alias used by some compilers
  branch_id: string;
  path_id: string;
  lineage_id: string;
  depth: number;
  expandable_directions: Array<'next' | 'prev' | 'neighbors'>;
  is_seed?: boolean;
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
  layout_hints: LayoutHints;
  chain_context: ChainContext;
  pagination?: PaginationMeta;
  asset_context?: AssetContext;
}

// ---------------------------------------------------------------------------
// Session API shapes
// ---------------------------------------------------------------------------

export interface SessionCreateRequest {
  seed_address: string;
  chain: string;
  label?: string;
}

export interface SessionCreateResponse {
  session_id: string;
  root_node: InvestigationNode;
}

export interface ExpandRequest {
  node_id: string;
  operation: 'expand_next' | 'expand_prev' | 'expand_neighbors';
  chain?: string;
  max_results?: number;
  min_fiat_value?: number;
  asset_filter?: string[];
  cursor?: string;
}

export interface BridgeHopStatusResponse {
  hop_id: string;
  status: 'pending' | 'completed' | 'failed';
  destination_tx_hash?: string;
  destination_chain?: string;
  destination_address?: string;
}
