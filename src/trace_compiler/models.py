"""
Investigation-view graph models — Pydantic schemas for ExpansionResponse v2.

These are the canonical data shapes for all investigation-grade expansion
operations.  The frontend depends on this contract.  Changing field names or
types here requires a coordinated frontend update.

Reference: PHASE3_IMPLEMENTATION_SPEC.md Section 3 (Investigation-View Graph
Model) and Section 6 (Graph Expansion API Spec).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import AliasChoices
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


# ---------------------------------------------------------------------------
# Node type literals
# ---------------------------------------------------------------------------

NodeType = Literal[
    "address",
    "entity",
    "service",
    "bridge_hop",
    "swap_event",
    "lightning_channel_open",
    "lightning_channel_close",
    "btc_sidechain_peg_in",
    "btc_sidechain_peg_out",
    "atomic_swap",
    "utxo",
    "solana_instruction",
    "cluster_summary",
]

ActivityType = Literal[
    "bridge",
    "service_interaction",
    "dex_interaction",
    "mixer_interaction",
    "router_interaction",
    "cex_interaction",
    "lightning_channel_open",
    "lightning_channel_close",
    "btc_sidechain_peg_in",
    "btc_sidechain_peg_out",
    "atomic_swap",
]

EdgeType = Literal[
    "transfer",
    "bridge_source",
    "bridge_dest",
    "swap_input",
    "swap_output",
    "cluster_member",
    "service_deposit",
    "service_receipt",
]

OperationType = Literal[
    "expand_next",
    "expand_prev",
    "expand_neighbors",
    "expand_bridge",
    "expand_utxo",
    "expand_solana_tx",
    "collapse_branch",
    "hide_node",
    "search",
]


# ---------------------------------------------------------------------------
# Type-specific node data payloads
# ---------------------------------------------------------------------------


class AddressNodeData(BaseModel):
    """Account-based or script-address specific fields."""

    address: str
    address_type: str  # "eoa" | "contract" | "multisig" | "program" | "pda" | ...
    tx_count: Optional[int] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    is_coinjoin_halt: Optional[bool] = None
    # Contract / program deployment metadata.
    is_contract: bool = False
    deployer: Optional[str] = None          # Address that deployed the contract.
    deployment_tx: Optional[str] = None     # Deployment transaction hash (EVM).
    upgrade_authority: Optional[str] = None  # Solana upgradeable-loader authority.
    deployer_entity: Optional[str] = None   # Resolved entity name for the deployer.


class EntityNodeData(BaseModel):
    """Entity (VASP / named actor) specific fields."""

    address_count: int
    chain_presence: List[str]
    verified: bool


class ServiceNodeData(BaseModel):
    """On-chain protocol / service specific fields."""

    protocol_id: str
    service_type: str  # "bridge" | "dex" | "mixer" | "router" | "cex" | "lending"
    known_contracts: List[str]


class BridgeHopData(BaseModel):
    """Cross-chain bridge hop correlation data."""

    model_config = ConfigDict(populate_by_name=True)

    hop_id: str
    protocol_id: str
    mechanism: str   # "lock_mint" | "burn_release" | "native_amm" | "solver" | "liquidity"
    source_chain: str
    destination_chain: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_chain", "dest_chain"),
    )
    source_asset: str
    destination_asset: str = Field(
        validation_alias=AliasChoices("destination_asset", "dest_asset"),
    )
    source_amount: float
    destination_amount: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("destination_amount", "dest_amount"),
    )
    time_delta_seconds: Optional[float] = None
    correlation_confidence: float = Field(
        validation_alias=AliasChoices("correlation_confidence", "correlation_conf"),
    )
    status: str  # "pending" | "completed" | "failed"
    is_same_asset: bool


class SwapEventData(BaseModel):
    """DEX / AMM swap event data."""

    model_config = ConfigDict(populate_by_name=True)

    swap_id: Optional[str] = None
    protocol_id: str
    chain: Optional[str] = None
    input_asset: str = Field(validation_alias=AliasChoices("input_asset", "in_asset"))
    input_amount: float = Field(validation_alias=AliasChoices("input_amount", "in_amount"))
    input_fiat: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("input_fiat", "in_fiat"),
    )
    output_asset: str = Field(validation_alias=AliasChoices("output_asset", "out_asset"))
    output_amount: float = Field(
        validation_alias=AliasChoices("output_amount", "out_amount"),
    )
    output_fiat: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("output_fiat", "out_fiat"),
    )
    exchange_rate: Optional[float] = None
    route_summary: Optional[str] = None
    tx_hash: Optional[str] = None
    timestamp: Optional[str] = None


class LightningChannelOpenData(BaseModel):
    """Lightning channel funding / open event data."""

    channel_id: str
    funding_tx_hash: str
    funding_vout: Optional[int] = None
    short_channel_id: Optional[str] = None
    capacity_btc: float
    local_pubkey: Optional[str] = None
    remote_pubkey: Optional[str] = None
    local_alias: Optional[str] = None
    remote_alias: Optional[str] = None
    is_private: Optional[bool] = None
    status: str = "open"


class LightningChannelCloseData(BaseModel):
    """Lightning channel closure event data."""

    channel_id: str
    close_tx_hash: str
    close_type: str = "unknown"
    settled_btc: Optional[float] = None
    local_pubkey: Optional[str] = None
    remote_pubkey: Optional[str] = None
    local_alias: Optional[str] = None
    remote_alias: Optional[str] = None
    status: str = "closed"


class BtcSidechainPegData(BaseModel):
    """Bitcoin-sidechain peg event data."""

    sidechain: str  # "liquid" | "rootstock" | "stacks"
    bitcoin_tx_hash: Optional[str] = None
    sidechain_tx_hash: Optional[str] = None
    peg_address_or_contract: Optional[str] = None
    asset_in: str
    asset_out: str
    amount_btc: Optional[float] = None
    amount_sidechain: Optional[float] = None
    mechanism: str = "bridge"
    confidence: float = 0.0
    status: str = "observed"


class AtomicSwapData(BaseModel):
    """Cross-chain or cross-domain atomic swap / HTLC event data."""

    swap_id: str
    protocol_id: Optional[str] = None
    source_chain: str
    destination_chain: str
    source_tx_hash: Optional[str] = None
    destination_tx_hash: Optional[str] = None
    hashlock: Optional[str] = None
    timelock: Optional[int] = None
    source_asset: str
    destination_asset: str
    source_amount: Optional[float] = None
    destination_amount: Optional[float] = None
    state: str = "partial"


class UTXONodeData(BaseModel):
    """Bitcoin UTXO output data."""

    tx_hash: str
    output_index: int
    value_satoshis: int
    value_btc: float
    script_type: str
    is_probable_change: bool
    is_coinjoin_halt: bool
    is_spent: bool


class SolanaInstructionData(BaseModel):
    """Solana instruction decomposition data."""

    program_id: str
    program_name: str
    instruction_type: str
    decoded_args: Dict[str, Any] = {}
    ix_index: int
    tx_signature: str


class ClusterSummaryData(BaseModel):
    """Collapsed subtree summary node data."""

    collapsed_node_ids: List[str]
    node_count: int
    dominant_type: str
    risk_max: float
    total_value_fiat: Optional[float] = None


class ActivitySummary(BaseModel):
    """Transaction-centric summary for graph activity nodes and edges."""

    activity_type: ActivityType
    title: str
    protocol_id: Optional[str] = None
    protocol_type: Optional[str] = None
    tx_hash: Optional[str] = None
    tx_chain: Optional[str] = None
    timestamp: Optional[str] = None
    direction: Optional[str] = None
    status: Optional[str] = None
    contract_address: Optional[str] = None
    source_chain: Optional[str] = None
    destination_chain: Optional[str] = None
    source_tx_hash: Optional[str] = None
    destination_tx_hash: Optional[str] = None
    order_id: Optional[str] = None
    asset_symbol: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    value_native: Optional[float] = None
    value_fiat: Optional[float] = None
    source_asset: Optional[str] = None
    destination_asset: Optional[str] = None
    source_amount: Optional[float] = None
    destination_amount: Optional[float] = None
    route_summary: Optional[str] = None
    method_name: Optional[str] = None


# ---------------------------------------------------------------------------
# InvestigationNode
# ---------------------------------------------------------------------------


class InvestigationNode(BaseModel):
    """A single node in the investigation-view graph.

    Every node carries lineage metadata (branch_id, path_id, depth,
    lineage_id) that uniquely identifies HOW this node was reached in the
    current investigation session, in addition to WHAT the node represents
    (node_id, node_type, and type-specific payload).
    """

    # Identity
    node_id: str  # "{chain}:{type}:{identifier}" — deterministic, session-stable
    lineage_id: str  # sha256("{session_id}:{branch_id}:{path_id}:{depth}")
    node_type: NodeType

    # Branch / path tracking
    branch_id: str
    path_id: str
    depth: int

    # Display
    display_label: str
    display_sublabel: Optional[str] = None
    display_group: Optional[str] = None

    # Risk & compliance
    risk_score: float = 0.0
    risk_factors: List[str] = []
    sanctioned: bool = False
    sanctions_list: Optional[str] = None

    # Attribution
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_type: Optional[str] = None
    entity_category: Optional[str] = None
    attribution_conf: Optional[float] = None

    # Financial snapshot
    balance_native: Optional[float] = None
    balance_fiat: Optional[float] = None
    fiat_currency: str = "USD"

    # Chain
    chain: str

    # Expansion state
    is_expanded: bool = False
    expandable_directions: List[str] = []  # "prev" | "next" | "neighbors"
    child_count_hint: Optional[int] = None

    # Investigation state (session-local)
    is_pinned: bool = False
    is_hidden: bool = False
    is_highlighted: bool = False
    investigator_label: Optional[str] = None

    # Type-specific payloads (discriminated by node_type)
    address_data: Optional[AddressNodeData] = None
    entity_data: Optional[EntityNodeData] = None
    service_data: Optional[ServiceNodeData] = None
    bridge_hop_data: Optional[BridgeHopData] = None
    swap_event_data: Optional[SwapEventData] = None
    lightning_channel_open_data: Optional[LightningChannelOpenData] = None
    lightning_channel_close_data: Optional[LightningChannelCloseData] = None
    btc_sidechain_peg_data: Optional[BtcSidechainPegData] = None
    atomic_swap_data: Optional[AtomicSwapData] = None
    utxo_data: Optional[UTXONodeData] = None
    instruction_data: Optional[SolanaInstructionData] = None
    cluster_summary: Optional[ClusterSummaryData] = None
    activity_summary: Optional[ActivitySummary] = None


# ---------------------------------------------------------------------------
# InvestigationEdge
# ---------------------------------------------------------------------------


class InvestigationEdge(BaseModel):
    """A directed edge in the investigation-view graph.

    ``edge_id`` is deterministic: sha256 of source + target + branch + tx_hash.
    Two branches reaching the same A→B transfer produce the same ``edge_id``
    only if they share the same ``branch_id``; otherwise they are distinct
    edges, allowing the frontend to display the same economic event in
    multiple branch contexts.
    """

    edge_id: str  # deterministic
    source_node_id: str
    target_node_id: str

    branch_id: str
    path_id: str
    edge_type: EdgeType

    # Financial
    value_native: Optional[float] = None
    value_fiat: Optional[float] = None
    asset_symbol: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    asset_chain: Optional[str] = None

    # Provenance
    tx_hash: Optional[str] = None
    tx_chain: Optional[str] = None
    block_number: Optional[int] = None
    timestamp: Optional[str] = None

    # Display
    is_highlighted: bool = False
    is_suspected_change: bool = False  # Bitcoin change output
    taint_percentage: Optional[float] = None  # 0.0–1.0 if taint model applied
    direction: str = "forward"  # "forward" | "backward" | "lateral"
    activity_summary: Optional[ActivitySummary] = None


# ---------------------------------------------------------------------------
# Layout hints
# ---------------------------------------------------------------------------


class LayoutHints(BaseModel):
    """Frontend layout suggestions from the trace compiler.

    The frontend renderer (ELK Layered) uses these as soft hints; it may
    override them based on node positions already fixed by the investigator.
    """

    suggested_layout: str = "layered"  # "layered" | "force" | "hierarchical"
    anchor_node_ids: List[str] = []
    new_branch_root_id: Optional[str] = None
    collapse_candidates: List[str] = []


# ---------------------------------------------------------------------------
# Chain and pagination context
# ---------------------------------------------------------------------------


class ChainContext(BaseModel):
    """Chain metadata attached to expansion responses."""

    primary_chain: str
    chains_present: List[str]


class PaginationMeta(BaseModel):
    """Pagination metadata for large result sets."""

    page_size: int = 25
    max_results: int = 100
    has_more: bool = False
    next_token: Optional[str] = None


class AssetContext(BaseModel):
    """Asset summary attached to expansion responses."""

    assets_present: List[str] = []
    total_value_fiat: Optional[float] = None


# ---------------------------------------------------------------------------
# ExpansionResponse v2
# ---------------------------------------------------------------------------


class ExpansionResponseV2(BaseModel):
    """Investigation-grade expansion response — v2 contract.

    This is the canonical response shape for ALL expansion operations issued
    by the trace compiler.  The Graph API serializes this as its response.
    The frontend must handle this shape for all endpoints that return graph
    data.

    Lineage fields (branch_id, parent_branch_id, expansion_depth) are
    guaranteed stable within a session — expanding the same node in the same
    session always produces the same branch_id.

    ``added_nodes`` and ``added_edges`` use the full ``InvestigationNode``
    and ``InvestigationEdge`` schemas.  Legacy ``GraphResponse`` nodes are NOT
    mixed into this response (ADR-004).
    """

    operation_id: str
    operation_type: OperationType
    session_id: str

    # Seed context
    seed_node_id: str
    seed_lineage_id: Optional[str] = None

    # Lineage
    branch_id: str
    parent_branch_id: Optional[str] = None
    expansion_depth: int

    # Graph delta
    added_nodes: List[InvestigationNode] = []
    added_edges: List[InvestigationEdge] = []
    removed_node_ids: List[str] = []
    updated_nodes: List[InvestigationNode] = []

    # Pagination
    has_more: bool = False
    continuation_token: Optional[str] = None
    pagination: PaginationMeta = Field(default_factory=PaginationMeta)

    # Ingest signalling — True when expansion was empty because historical data
    # is not yet in the event store; the frontend should poll and retry.
    ingest_pending: bool = False

    # Context
    layout_hints: LayoutHints = Field(default_factory=LayoutHints)
    chain_context: ChainContext = Field(
        default_factory=lambda: ChainContext(primary_chain="unknown", chains_present=[])
    )
    asset_context: AssetContext = Field(default_factory=AssetContext)

    timestamp: datetime


# ---------------------------------------------------------------------------
# Session models
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    """Request body for POST /api/v1/graph/sessions."""

    case_id: Optional[str] = None
    seed_address: str
    seed_chain: str


class SessionCreateResponse(BaseModel):
    """Response for POST /api/v1/graph/sessions."""

    session_id: str
    root_node: InvestigationNode
    created_at: datetime


class NodeStateSnapshot(BaseModel):
    """Per-node state for a session snapshot."""

    node_id: str
    lineage_id: str
    is_pinned: bool = False
    is_hidden: bool = False
    branch_id: str
    position_hint: Optional[Dict[str, float]] = None  # {"x": ..., "y": ...}


class SessionSnapshotRequest(BaseModel):
    """Request body for POST /api/v1/graph/sessions/{session_id}/snapshot."""

    node_states: List[NodeStateSnapshot]


class SessionSnapshotResponse(BaseModel):
    """Response for session snapshot save."""

    snapshot_id: str
    saved_at: datetime


# ---------------------------------------------------------------------------
# Expand request
# ---------------------------------------------------------------------------


class ExpandOptions(BaseModel):
    """Expansion options for POST /api/v1/graph/sessions/{session_id}/expand."""

    depth: int = Field(default=1, ge=1, le=3)
    asset_filter: List[str] = []
    chain_filter: List[str] = []
    min_value_fiat: Optional[float] = None
    max_results: int = Field(default=25, ge=1, le=100)
    include_services: bool = True
    follow_bridges: bool = True
    continuation_token: Optional[str] = None
    page_size: int = Field(default=25, ge=1, le=50)


class ExpandRequest(BaseModel):
    """Request body for POST /api/v1/graph/sessions/{session_id}/expand."""

    operation_type: OperationType
    seed_node_id: str
    seed_lineage_id: Optional[str] = None
    options: ExpandOptions = Field(default_factory=ExpandOptions)


# ---------------------------------------------------------------------------
# Bridge hop status
# ---------------------------------------------------------------------------


class BridgeHopStatusResponse(BaseModel):
    """Response for GET /api/v1/graph/sessions/{session_id}/hops/{hop_id}/status."""

    model_config = ConfigDict(populate_by_name=True)

    hop_id: str
    status: str  # "pending" | "completed" | "failed" | "expired"
    destination_chain: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_chain", "dest_chain"),
    )
    destination_tx_hash: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_tx_hash", "dest_tx_hash"),
    )
    destination_address: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_address", "dest_address"),
    )
    correlation_confidence: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("correlation_confidence", "correlation_conf"),
    )
    updated_at: datetime


class IngestStatusResponse(BaseModel):
    """Response for GET /api/v1/graph/sessions/{session_id}/ingest/status.

    Returned by the frontend's ingest-pending poller.  The frontend calls this
    endpoint every 5 seconds after expansion returns ``ingest_pending=True``.
    When ``status`` transitions to ``"completed"``, the frontend retries the
    expansion to load the newly-ingested activity.
    """

    address: str
    blockchain: str
    # "pending" | "running" | "completed" | "failed" | "not_found"
    status: str
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tx_count: Optional[int] = None
    error: Optional[str] = None
