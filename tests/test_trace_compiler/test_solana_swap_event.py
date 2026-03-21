"""
Unit tests for Solana swap_event detection.

Covers:
- ServiceClassifier recognises Raydium, Orca, Jupiter, OpenBook, Meteora, Phoenix
- _maybe_build_solana_swap_event returns a swap_event node when both SPL legs present
- Falls back to None when legs are missing (caller uses generic service node)
- Identity swaps (same asset in/out) are rejected
- ATA resolution is applied to leg addresses before comparison
- _build_graph wires the swap promotion correctly (end-to-end unit)
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from src.trace_compiler.chains.solana import SolanaChainCompiler
from src.trace_compiler.services.service_classifier import ServiceClassifier


# ---------------------------------------------------------------------------
# ServiceClassifier — Solana DEX registry
# ---------------------------------------------------------------------------

class TestSolanaServiceRegistry:
    def setup_method(self):
        self.svc = ServiceClassifier()

    def test_raydium_amm_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        )

    def test_raydium_clmm_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
        )

    def test_orca_whirlpool_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
        )

    def test_orca_v2_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"
        )

    def test_jupiter_v6_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
        )

    def test_jupiter_v4_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"
        )

    def test_openbook_serum_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX"
        )

    def test_openbook_v2_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb"
        )

    def test_meteora_dlmm_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB"
        )

    def test_phoenix_recognised(self):
        assert self.svc.is_service_contract(
            "solana", "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY"
        )

    def test_unknown_program_not_recognised(self):
        assert not self.svc.is_service_contract(
            "solana", "11111111111111111111111111111111"
        )

    def test_jupiter_service_type_is_aggregator(self):
        record = self.svc.get_record(
            "solana", "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
        )
        assert record is not None
        assert record.service_type == "aggregator"

    def test_raydium_service_type_is_dex(self):
        record = self.svc.get_record(
            "solana", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
        )
        assert record is not None
        assert record.service_type == "dex"

    def test_evm_contract_not_matched_on_solana(self):
        # Uniswap V3 router should not resolve on the solana chain bucket.
        assert not self.svc.is_service_contract(
            "solana", "0xe592427a0aece92de3edee1f18e0157c05861564"
        )


# ---------------------------------------------------------------------------
# _maybe_build_solana_swap_event
# ---------------------------------------------------------------------------

_SEED = "SeedWalletAddressAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_SEED_NODE = f"solana:address:{_SEED}"
_RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
_TX = "5FHwkrdxntdK24hgQU8qgBjn35Y1zwhz1GZwCkP2UJnM"


def _make_compiler(spl_rows):
    """Return a SolanaChainCompiler with a mock PG pool returning spl_rows."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[dict(r) for r in spl_rows])
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    compiler = SolanaChainCompiler(postgres_pool=mock_pool)
    return compiler


_SPL_LEGS_USDC_SOL = [
    # Seed sends 100 USDC out
    {"from_address": _SEED, "to_address": _RAYDIUM, "amount_normalized": 100.0, "asset_symbol": "USDC", "canonical_asset_id": None},
    # Seed receives 0.8 SOL in (wSOL)
    {"from_address": _RAYDIUM, "to_address": _SEED, "amount_normalized": 0.8, "asset_symbol": "WSOL", "canonical_asset_id": None},
]


class TestMaybeBuildSolanaSwapEvent:
    @pytest.mark.asyncio
    async def test_returns_swap_node_when_both_legs_present(self):
        compiler = _make_compiler(_SPL_LEGS_USDC_SOL)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX,
            seed_address=_SEED,
            seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM,
            protocol_id="raydium_amm",
            protocol_label="Raydium AMM",
            protocol_type="dex",
            session_id="sess-1",
            branch_id="br-1",
            path_id="path-1",
            depth=1,
            timestamp="2025-01-01T00:00:00Z",
            ata_map={},
        )
        assert result is not None
        nodes, edges = result
        assert len(nodes) == 1
        assert nodes[0].node_type == "swap_event"
        assert nodes[0].swap_event_data is not None
        assert nodes[0].swap_event_data.input_asset == "USDC"
        assert nodes[0].swap_event_data.output_asset == "WSOL"
        assert nodes[0].swap_event_data.input_amount == pytest.approx(100.0)
        assert nodes[0].swap_event_data.output_amount == pytest.approx(0.8)
        assert nodes[0].swap_event_data.protocol_id == "raydium_amm"
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_edge_types_are_swap_input_and_output(self):
        compiler = _make_compiler(_SPL_LEGS_USDC_SOL)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is not None
        _, edges = result
        edge_types = {e.edge_type for e in edges}
        assert "swap_input" in edge_types
        assert "swap_output" in edge_types

    @pytest.mark.asyncio
    async def test_returns_none_when_no_spl_legs(self):
        compiler = _make_compiler([])
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_outgoing_leg_missing(self):
        # Only incoming leg — no outgoing USDC from seed.
        legs = [
            {"from_address": _RAYDIUM, "to_address": _SEED, "amount_normalized": 0.8,
             "asset_symbol": "WSOL", "canonical_asset_id": None},
        ]
        compiler = _make_compiler(legs)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_identity_swap_rejected(self):
        # Same asset, same amount both ways — not a real swap.
        legs = [
            {"from_address": _SEED, "to_address": _RAYDIUM, "amount_normalized": 100.0,
             "asset_symbol": "USDC", "canonical_asset_id": None},
            {"from_address": _RAYDIUM, "to_address": _SEED, "amount_normalized": 100.0,
             "asset_symbol": "USDC", "canonical_asset_id": None},
        ]
        compiler = _make_compiler(legs)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_ata_resolved_addresses_are_used(self):
        """If seed's ATA appears in the leg, it should be resolved to the seed wallet."""
        _ATA = "AtaAddressForSeedAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        legs = [
            {"from_address": _ATA, "to_address": _RAYDIUM, "amount_normalized": 50.0,
             "asset_symbol": "USDC", "canonical_asset_id": None},
            {"from_address": _RAYDIUM, "to_address": _ATA, "amount_normalized": 0.4,
             "asset_symbol": "SOL", "canonical_asset_id": None},
        ]
        compiler = _make_compiler(legs)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None,
            ata_map={_ATA: _SEED},  # ATA resolves to seed
        )
        assert result is not None
        nodes, _ = result
        assert nodes[0].swap_event_data.input_asset == "USDC"
        assert nodes[0].swap_event_data.output_asset == "SOL"

    @pytest.mark.asyncio
    async def test_exchange_rate_computed(self):
        compiler = _make_compiler(_SPL_LEGS_USDC_SOL)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is not None
        rate = result[0][0].swap_event_data.exchange_rate
        assert rate == pytest.approx(0.8 / 100.0)

    @pytest.mark.asyncio
    async def test_activity_summary_populated(self):
        compiler = _make_compiler(_SPL_LEGS_USDC_SOL)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address=_RAYDIUM, protocol_id="raydium_amm",
            protocol_label="Raydium AMM", protocol_type="dex",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp="2025-01-01T00:00:00Z", ata_map={},
        )
        assert result is not None
        summary = result[0][0].activity_summary
        assert summary is not None
        assert summary.activity_type == "dex_interaction"
        assert summary.protocol_id == "raydium_amm"

    @pytest.mark.asyncio
    async def test_aggregator_activity_type(self):
        compiler = _make_compiler(_SPL_LEGS_USDC_SOL)
        result = await compiler._maybe_build_solana_swap_event(
            tx_hash=_TX, seed_address=_SEED, seed_node_id=_SEED_NODE,
            program_address="JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
            protocol_id="jupiter", protocol_label="Jupiter Aggregator",
            protocol_type="aggregator",
            session_id="s", branch_id="b", path_id="p", depth=1,
            timestamp=None, ata_map={},
        )
        assert result is not None
        assert result[0][0].activity_summary.activity_type == "router_interaction"
