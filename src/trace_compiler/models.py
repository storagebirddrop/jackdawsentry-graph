"""
Investigation-view graph models — Pydantic schemas for ExpansionResponse v2.

These are the canonical data shapes for all investigation-grade expansion
operations.  The frontend depends on this contract.  Changing field names or
types here requires a coordinated frontend update.

These models are the durable investigation graph contract used by the backend,
frontend, and regression suite.
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
from pydantic import computed_field


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

ExpansionDataSource = Literal[
    "event_store",
    "neo4j_fallback",
    "live_history",
]

AssetSelectorMode = Literal["all", "native", "asset"]


# ---------------------------------------------------------------------------
# Type-specific node data payloads
# ---------------------------------------------------------------------------


class AddressNodeData(BaseModel):
    """Account-based or script-address specific fields."""

    address: str
    address_type: str  # "eoa" | "contract" | "multisig" | "program" | "pda" | ...
    chain: Optional[str] = None
    entity_id: Optional[str] = None
    entity_name: Optional[str] = None
    entity_category: Optional[str] = None
    risk_score: Optional[float] = None
    is_sanctioned: Optional[bool] = None
    is_mixer: Optional[bool] = None
    label: Optional[str] = None
    fiat_value_usd: Optional[float] = None
    tx_count: Optional[int] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    is_coinjoin_halt: Optional[bool] = None


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
    destination_address: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_address", "dest_address"),
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
    destination_tx_hash: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("destination_tx_hash", "dest_tx_hash"),
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
    chain: Optional[str] = None
    protocol_id: str
    in_asset: str = Field(validation_alias=AliasChoices("in_asset", "input_asset"))
    in_amount: float = Field(validation_alias=AliasChoices("in_amount", "input_amount"))
    in_fiat: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("in_fiat", "input_fiat"),
    )
    out_asset: str = Field(validation_alias=AliasChoices("out_asset", "output_asset"))
    out_amount: float = Field(validation_alias=AliasChoices("out_amount", "output_amount"))
    out_fiat: Optional[float] = Field(
        default=None,
        validation_alias=AliasChoices("out_fiat", "output_fiat"),
    )
    exchange_rate: Optional[float] = None
    route_summary: Optional[str] = None
    tx_hash: str
    timestamp: Optional[str] = None

    @computed_field(return_type=str)
    @property
    def input_asset(self) -> str:
        return self.in_asset

    @computed_field(return_type=float)
    @property
    def input_amount(self) -> float:
        return self.in_amount

    @computed_field(return_type=Optional[float])
    @property
    def input_fiat(self) -> Optional[float]:
        return self.in_fiat

    @computed_field(return_type=str)
    @property
    def output_asset(self) -> str:
        return self.out_asset

    @computed_field(return_type=float)
    @property
    def output_amount(self) -> float:
        return self.out_amount

    @computed_field(return_type=Optional[float])
    @property
    def output_fiat(self) -> Optional[float]:
        return self.out_fiat


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
    destination_address: Optional[str] = None
    order_id: Optional[str] = None
    asset_symbol: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    chain_asset_id: Optional[str] = None
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
    chain_asset_id: Optional[str] = None
    asset_address: Optional[str] = None
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
    canonical_asset_ids: List[str] = []
    total_value_fiat: Optional[float] = None


class AssetSelector(BaseModel):
    """Single-asset expansion selector shared by the UI and compiler."""

    mode: AssetSelectorMode = "all"
    chain: str
    chain_asset_id: Optional[str] = None
    asset_symbol: Optional[str] = None
    canonical_asset_id: Optional[str] = None


class AssetOption(BaseModel):
    """One asset-selection choice exposed to the frontend."""

    mode: AssetSelectorMode = "all"
    chain: str
    chain_asset_id: Optional[str] = None
    asset_symbol: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    display_label: str


class ExpansionEmptyState(BaseModel):
    """Context for empty expansion responses.

    Helps the frontend distinguish between:
    - no indexed data in the current graph dataset,
    - address not observed on-chain,
    - chains where live address-history lookup is unavailable, and
    - chains where a live lookup was attempted and still found no activity.
    """

    reason: str
    message: str
    chain: str
    address: Optional[str] = None
    operation_type: Optional[OperationType] = None
    live_lookup_supported: bool = False
    observed_on_chain: Optional[bool] = None
    known_tx_count: Optional[int] = None


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

    # Context
    layout_hints: LayoutHints = Field(default_factory=LayoutHints)
    chain_context: ChainContext = Field(
        default_factory=lambda: ChainContext(primary_chain="unknown", chains_present=[])
    )
    asset_context: AssetContext = Field(default_factory=AssetContext)
    data_sources: List[ExpansionDataSource] = []
    integrity_warning: Optional[str] = None
    empty_state: Optional[ExpansionEmptyState] = None
    ingest_pending: bool = False

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


class AssetCatalogItem(BaseModel):
    """Session-scoped asset metadata for the explorer picker."""

    asset_key: str
    symbol: str
    display_name: Optional[str] = None
    canonical_asset_id: Optional[str] = None
    canonical_symbol: Optional[str] = None
    identity_status: str = "unknown"
    variant_kind: str = "unknown"
    blockchains: List[str] = []
    token_standards: List[str] = []
    observed_transfer_count: int = 0
    last_seen_at: Optional[datetime] = None
    sample_asset_address: Optional[str] = None
    is_native: bool = False


class AssetCatalogResponse(BaseModel):
    """Response for GET /api/v1/graph/sessions/{session_id}/assets."""

    session_id: str
    seed_chain: str
    chains_present: List[str]
    items: List[AssetCatalogItem]
    generated_at: datetime


class WorkspacePosition(BaseModel):
    """Stored canvas position for a graph node."""

    x: float
    y: float


class WorkspaceBranchSnapshot(BaseModel):
    """Serialized branch metadata for session restore."""

    branchId: str
    color: str
    seedNodeId: str
    minDepth: int
    maxDepth: int
    nodeCount: int


class WorkspacePreferencesSnapshot(BaseModel):
    """Serialized workspace preferences stored alongside a graph snapshot."""

    selectedAssets: List[str]
    pinnedAssetKeys: List[str]
    assetCatalogScope: Literal["session", "visible"]


class WorkspaceSnapshotV1(BaseModel):
    """Authoritative server-backed workspace snapshot."""

    schema_version: int = 1
    revision: int = 0
    sessionId: str
    nodes: List[InvestigationNode] = Field(default_factory=list)
    edges: List[InvestigationEdge] = Field(default_factory=list)
    positions: Dict[str, WorkspacePosition] = Field(default_factory=dict)
    branches: Optional[List[WorkspaceBranchSnapshot]] = None
    workspacePreferences: Optional[WorkspacePreferencesSnapshot] = None


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

    node_states: List[NodeStateSnapshot] = Field(default_factory=list)
    schema_version: int = 1
    revision: int = 0
    sessionId: Optional[str] = None
    nodes: Optional[List[InvestigationNode]] = None
    edges: Optional[List[InvestigationEdge]] = None
    positions: Dict[str, WorkspacePosition] = Field(default_factory=dict)
    branches: Optional[List[WorkspaceBranchSnapshot]] = None
    workspacePreferences: Optional[WorkspacePreferencesSnapshot] = None

    def has_workspace_payload(self) -> bool:
        return any(
            (
                self.sessionId is not None,
                self.nodes is not None,
                self.edges is not None,
                bool(self.positions),
                self.branches is not None,
                self.workspacePreferences is not None,
            )
        )

    def to_workspace_snapshot(self) -> WorkspaceSnapshotV1:
        if self.sessionId is None:
            raise ValueError("workspace snapshot payload requires sessionId")

        return WorkspaceSnapshotV1(
            schema_version=self.schema_version,
            revision=getattr(self, "revision", 0),
            sessionId=self.sessionId,
            nodes=self.nodes or [],
            edges=self.edges or [],
            positions=self.positions,
            branches=self.branches,
            workspacePreferences=self.workspacePreferences,
        )


class SessionSnapshotResponse(BaseModel):
    """Response for session snapshot save."""

    snapshot_id: str
    saved_at: datetime
    revision: int


class RecentSessionSummary(BaseModel):
    """Restore candidate summary for recent investigation sessions."""

    session_id: str
    seed_address: Optional[str] = None
    seed_chain: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    snapshot_saved_at: Optional[datetime] = None


class RecentSessionsResponse(BaseModel):
    """Response for recent session discovery."""

    items: List[RecentSessionSummary] = Field(default_factory=list)


class InvestigationSessionResponse(BaseModel):
    """Response for GET /api/v1/graph/sessions/{session_id}."""

    session_id: str
    seed_address: Optional[str] = None
    seed_chain: Optional[str] = None
    case_id: Optional[str] = None
    snapshot: Optional[Any] = None
    workspace: WorkspaceSnapshotV1
    restore_state: Literal["full", "legacy_bootstrap"]
    nodes: List[InvestigationNode] = Field(default_factory=list)
    edges: List[InvestigationEdge] = Field(default_factory=list)
    branch_map: Dict[str, WorkspaceBranchSnapshot] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    snapshot_saved_at: Optional[datetime] = None


class IngestStatusResponse(BaseModel):
    """Response for background address ingest polling."""

    address: str
    blockchain: str
    status: Literal["pending", "running", "completed", "failed", "not_found"]
    queued_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tx_count: Optional[int] = None
    error: Optional[str] = None


class TxResolveResponse(BaseModel):
    """Response for tx-hash resolution into trace seed context."""

    found: bool
    tx_hash: str
    blockchain: str
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    value_native: Optional[float] = None
    asset_symbol: Optional[str] = None
    timestamp: Optional[datetime] = None
    block_number: Optional[int] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Expand request
# ---------------------------------------------------------------------------


class ExpandOptions(BaseModel):
    """Expansion options for POST /api/v1/graph/sessions/{session_id}/expand."""

    depth: int = Field(default=1, ge=1, le=3)
    asset_filter: List[str] = []
    asset_selector: Optional[AssetSelector] = None
    chain_filter: List[str] = []
    tx_hashes: List[str] = []
    min_value_fiat: Optional[float] = None
    max_results: int = Field(default=25, ge=1, le=100)
    include_services: bool = True
    follow_bridges: bool = True
    continuation_token: Optional[str] = None
    page_size: int = Field(default=25, ge=1, le=50)
    time_from: Optional[datetime] = None  # ISO 8601 UTC, inclusive lower bound
    time_to: Optional[datetime] = None    # ISO 8601 UTC, inclusive upper bound


class ExpandRequest(BaseModel):
    """Request body for POST /api/v1/graph/sessions/{session_id}/expand."""

    operation_type: OperationType
    seed_node_id: str
    seed_lineage_id: Optional[str] = None
    options: ExpandOptions = Field(default_factory=ExpandOptions)


class AssetOptionsRequest(BaseModel):
    """Request body for POST /api/v1/graph/sessions/{session_id}/asset-options."""

    seed_node_id: str
    seed_lineage_id: Optional[str] = None


class AssetOptionsResponse(BaseModel):
    """Address-level asset options for selective expansion."""

    session_id: str
    seed_node_id: str
    seed_lineage_id: Optional[str] = None
    options: List[AssetOption] = []


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
