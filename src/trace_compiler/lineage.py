"""
Deterministic lineage ID computation functions.

All IDs produced here are stable: the same inputs always produce the same
output, across sessions, rebuilds, and server restarts.

These helpers define the stable ID rules used across sessions, rebuilds, and
tests.
"""

import hashlib
import uuid


def node_id(chain: str, node_type: str, identifier: str) -> str:
    """Return the canonical stable node ID for a graph node.

    Format: ``"{chain}:{type}:{canonical_identifier}"``

    The ``identifier`` must be in canonical form:
    - EVM addresses: lowercase hex (``0x...``)
    - Bitcoin addresses: Base58 as-is (case-sensitive)
    - Solana addresses: Base58 as-is (case-sensitive)
    - Transaction hashes: lowercase
    - swap_event / bridge_hop: sha256 of their deterministic inputs

    Args:
        chain:      Blockchain name (``"ethereum"``, ``"bitcoin"``, etc.)
        node_type:  Node type string (``"address"``, ``"bridge_hop"``, etc.)
        identifier: Canonical identifier for the node.

    Returns:
        Stable node ID string.
    """
    return f"{chain}:{node_type}:{identifier}"


def branch_id(session_id: str, seed_node_id: str, sequence: int) -> str:
    """Return a deterministic branch ID for an expansion operation.

    Expanding the same seed node in the same session always produces the
    same branch_id for a given sequence number.

    Args:
        session_id:   UUID of the investigation session.
        seed_node_id: node_id() of the node being expanded.
        sequence:     Monotonically increasing integer within the session.

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"{session_id}:{seed_node_id}:{sequence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def path_id(branch_id_val: str, sequence: int) -> str:
    """Return a deterministic path ID within a branch.

    Args:
        branch_id_val: branch_id() value for the enclosing branch.
        sequence:      Sequential path number within the branch.

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"{branch_id_val}:{sequence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def lineage_id(session_id: str, branch_id_val: str, path_id_val: str, depth: int) -> str:
    """Return the lineage ID encoding HOW a node was reached.

    Two branches reaching the same address will have the same node_id but
    different lineage_ids.

    Args:
        session_id:    UUID of the investigation session.
        branch_id_val: branch_id() value.
        path_id_val:   path_id() value.
        depth:         Integer hop count from the session root.

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"{session_id}:{branch_id_val}:{path_id_val}:{depth}"
    return hashlib.sha256(raw.encode()).hexdigest()


def edge_id(
    source_node_id: str,
    target_node_id: str,
    branch_id_val: str,
    tx_hash: str | None = None,
) -> str:
    """Return a deterministic edge ID.

    Args:
        source_node_id: node_id() of the source node.
        target_node_id: node_id() of the target node.
        branch_id_val:  branch_id() for the enclosing branch.
        tx_hash:        Optional transaction hash for provenance disambiguation.

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"{source_node_id}:{target_node_id}:{branch_id_val}:{tx_hash or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def swap_event_id(chain: str, tx_hash: str, ix_index: int) -> str:
    """Return the deterministic swap_id for a SwapEvent.

    Format from spec: sha256("{chain}:swap:{tx_hash}:{ix_index}")

    Args:
        chain:    Blockchain name.
        tx_hash:  Transaction hash of the swap.
        ix_index: Instruction index within the transaction (0 for EVM, ix_idx for Solana).

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"{chain}:swap:{tx_hash}:{ix_index}"
    return hashlib.sha256(raw.encode()).hexdigest()


def bridge_hop_id(source_chain: str, source_tx_hash: str) -> str:
    """Return the deterministic hop_id for a BridgeHop.

    Format from spec: sha256("bridge:{source_chain}:{source_tx_hash}")

    Args:
        source_chain:    Chain where the bridge ingress occurred.
        source_tx_hash:  Transaction hash of the ingress.

    Returns:
        sha256 hex digest (64 chars).
    """
    raw = f"bridge:{source_chain}:{source_tx_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


def new_operation_id() -> str:
    """Return a new random UUID for an expansion operation."""
    return str(uuid.uuid4())


def new_session_id() -> str:
    """Return a new random UUID for an investigation session."""
    return str(uuid.uuid4())
