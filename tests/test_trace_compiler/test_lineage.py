"""
Unit tests for src/trace_compiler/lineage.py.

Verifies determinism, format, and uniqueness properties of all ID functions.
"""

import hashlib

import pytest

from src.trace_compiler.lineage import (
    branch_id,
    bridge_hop_id,
    edge_id,
    lineage_id,
    new_operation_id,
    new_session_id,
    node_id,
    path_id,
    swap_event_id,
)


class TestNodeId:
    def test_format(self):
        nid = node_id("ethereum", "address", "0xabc")
        assert nid == "ethereum:address:0xabc"

    def test_deterministic(self):
        assert node_id("bitcoin", "address", "1A1z") == node_id("bitcoin", "address", "1A1z")

    def test_different_chains_produce_different_ids(self):
        assert node_id("ethereum", "address", "0xabc") != node_id("solana", "address", "0xabc")


class TestBranchId:
    def test_is_sha256_hex(self):
        bid = branch_id("sess1", "eth:address:0xabc", 0)
        assert len(bid) == 64
        int(bid, 16)  # must be valid hex

    def test_deterministic(self):
        assert (
            branch_id("sess1", "eth:address:0xabc", 0)
            == branch_id("sess1", "eth:address:0xabc", 0)
        )

    def test_different_sequence_produces_different_id(self):
        assert (
            branch_id("sess1", "eth:address:0xabc", 0)
            != branch_id("sess1", "eth:address:0xabc", 1)
        )

    def test_different_session_produces_different_id(self):
        assert (
            branch_id("sess1", "eth:address:0xabc", 0)
            != branch_id("sess2", "eth:address:0xabc", 0)
        )


class TestPathId:
    def test_is_sha256_hex(self):
        pid = path_id("branchval", 0)
        assert len(pid) == 64

    def test_deterministic(self):
        assert path_id("branchval", 0) == path_id("branchval", 0)

    def test_different_sequence(self):
        assert path_id("branchval", 0) != path_id("branchval", 1)


class TestLineageId:
    def test_is_sha256_hex(self):
        lid = lineage_id("sess", "branch", "path", 1)
        assert len(lid) == 64

    def test_deterministic(self):
        assert lineage_id("s", "b", "p", 2) == lineage_id("s", "b", "p", 2)

    def test_different_depth_produces_different_id(self):
        assert lineage_id("s", "b", "p", 0) != lineage_id("s", "b", "p", 1)

    def test_encodes_how_not_what(self):
        """Same node reached via two different branches → different lineage."""
        l1 = lineage_id("s", "branch_a", "path_a", 2)
        l2 = lineage_id("s", "branch_b", "path_b", 2)
        assert l1 != l2


class TestEdgeId:
    def test_deterministic(self):
        assert (
            edge_id("n1", "n2", "b1", "txhash")
            == edge_id("n1", "n2", "b1", "txhash")
        )

    def test_no_tx_hash(self):
        eid = edge_id("n1", "n2", "b1")
        assert len(eid) == 64

    def test_different_tx_hash_produces_different_id(self):
        assert edge_id("n1", "n2", "b1", "tx1") != edge_id("n1", "n2", "b1", "tx2")


class TestSwapEventId:
    def test_is_sha256_of_canonical_form(self):
        expected = hashlib.sha256(b"ethereum:swap:0xtx:2").hexdigest()
        assert swap_event_id("ethereum", "0xtx", 2) == expected

    def test_deterministic(self):
        assert swap_event_id("solana", "sig123", 0) == swap_event_id("solana", "sig123", 0)


class TestBridgeHopId:
    def test_is_sha256_of_canonical_form(self):
        expected = hashlib.sha256(b"bridge:ethereum:0xtx").hexdigest()
        assert bridge_hop_id("ethereum", "0xtx") == expected

    def test_deterministic(self):
        assert bridge_hop_id("ethereum", "0xtx") == bridge_hop_id("ethereum", "0xtx")


class TestNewIds:
    def test_new_operation_id_is_uuid(self):
        import uuid
        uid = new_operation_id()
        uuid.UUID(uid)  # raises if not valid UUID

    def test_new_session_id_is_uuid(self):
        import uuid
        uuid.UUID(new_session_id())

    def test_new_operation_id_is_unique(self):
        assert new_operation_id() != new_operation_id()
