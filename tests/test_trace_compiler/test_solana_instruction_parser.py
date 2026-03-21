"""Unit tests for SolanaInstructionParser and related helpers.

Covers:
- _anchor_discriminant helper properties
- KNOWN_PROGRAMS new program ID entries
- _decode_orca_whirlpool: swap, twoHopSwap, short/empty data
- _decode_meteora_dlmm: swap, wrong discriminant, short data
- _decode_phoenix: swap, swapWithFreeFunds, unknown discriminant, empty data
- _decode_openbook_v2: placeTakeOrder, unknown discriminant
- parse_transaction_instructions dispatch for new programs
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import List

import pytest

from src.collectors.solana_instruction_parser import (
    KNOWN_PROGRAMS,
    ParsedInstruction,
    SolanaInstructionParser,
    _anchor_discriminant,
)


# ---------------------------------------------------------------------------
# Helper: compute the Anchor discriminant the same way the source does
# ---------------------------------------------------------------------------


def _disc(name: str) -> bytes:
    """Compute the Anchor 8-byte discriminant for *name*."""
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


# ---------------------------------------------------------------------------
# _anchor_discriminant helper tests
# ---------------------------------------------------------------------------


class TestAnchorDiscriminant:
    """Tests for the _anchor_discriminant helper function."""

    def test_returns_8_bytes(self):
        """Result must always be exactly 8 bytes."""
        result = _anchor_discriminant("swap")
        assert isinstance(result, bytes)
        assert len(result) == 8

    def test_deterministic_same_name(self):
        """Same input always produces the same output."""
        a = _anchor_discriminant("swap")
        b = _anchor_discriminant("swap")
        assert a == b

    def test_different_names_produce_different_results(self):
        """Different instruction names must not collide."""
        assert _anchor_discriminant("swap") != _anchor_discriminant("two_hop_swap")
        assert _anchor_discriminant("route") != _anchor_discriminant("swap")

    def test_jupiter_route_matches_hardcoded_value(self):
        """Jupiter v6 'route' discriminant matches the hard-coded constant."""
        expected = bytes([0xE5, 0x17, 0xCB, 0x97, 0x7A, 0xE3, 0xAD, 0x2A])
        assert _anchor_discriminant("route") == expected


# ---------------------------------------------------------------------------
# KNOWN_PROGRAMS entries for new programs
# ---------------------------------------------------------------------------


class TestKnownPrograms:
    """Tests that new program IDs are registered with correct labels."""

    def test_orca_whirlpool_primary(self):
        """Primary Orca Whirlpool program ID resolves to 'orca_whirlpool'."""
        assert KNOWN_PROGRAMS["whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"] == "orca_whirlpool"

    def test_orca_whirlpool_secondary(self):
        """Secondary Orca Whirlpool program ID also resolves to 'orca_whirlpool'."""
        assert KNOWN_PROGRAMS["9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"] == "orca_whirlpool"

    def test_meteora_dlmm(self):
        """Meteora DLMM program ID resolves to 'meteora_dlmm'."""
        assert KNOWN_PROGRAMS["Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB"] == "meteora_dlmm"

    def test_phoenix(self):
        """Phoenix program ID resolves to 'phoenix'."""
        assert KNOWN_PROGRAMS["PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY"] == "phoenix"

    def test_openbook_v2(self):
        """OpenBook v2 program ID resolves to 'openbook_v2'."""
        assert KNOWN_PROGRAMS["opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb"] == "openbook_v2"


# ---------------------------------------------------------------------------
# Byte builders for Orca Whirlpool instructions
# ---------------------------------------------------------------------------


def _orca_swap_bytes(amount: int = 1000, threshold: int = 990, a_to_b: bool = True) -> bytes:
    """Build valid Orca Whirlpool swap instruction bytes."""
    disc = _disc("swap")
    return (
        disc
        + struct.pack("<Q", amount)      # amount u64
        + struct.pack("<Q", threshold)   # other_amount_threshold u64
        + b"\x00" * 16                  # sqrt_price_limit u128
        + bytes([1])                     # amount_specified_is_input bool
        + bytes([1 if a_to_b else 0])    # a_to_b bool
    )  # total: 8 + 8 + 8 + 16 + 1 + 1 = 42 bytes


def _orca_two_hop_bytes(amount: int = 2000) -> bytes:
    """Build valid Orca Whirlpool twoHopSwap instruction bytes."""
    disc = _disc("two_hop_swap")
    return (
        disc
        + struct.pack("<Q", amount)   # amount u64
        + struct.pack("<Q", 980)      # other_amount_threshold_one u64
        + struct.pack("<Q", 970)      # other_amount_threshold_two u64
        + bytes([1])                  # amount_specified_is_input
        + bytes([1])                  # a_to_b_one
        + bytes([0])                  # a_to_b_two
    )  # total: 8 + 8 + 8 + 8 + 1 + 1 + 1 = 35 bytes


# ---------------------------------------------------------------------------
# _decode_orca_whirlpool tests
# ---------------------------------------------------------------------------


class TestDecodeOrcaWhirlpool:
    """Tests for SolanaInstructionParser._decode_orca_whirlpool."""

    def setup_method(self):
        self.parser = SolanaInstructionParser()
        self.program_id = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
        self.accounts = [f"acc{i}" for i in range(10)]

    def _call(self, data: bytes) -> ParsedInstruction:
        return self.parser._decode_orca_whirlpool(
            self.program_id, self.accounts, data, ""
        )

    def test_swap_ok_status(self):
        """Valid swap bytes produce decode_status=='ok'."""
        result = self._call(_orca_swap_bytes())
        assert result.decode_status == "ok"

    def test_swap_instruction_type(self):
        """Valid swap bytes produce instruction_type=='swap'."""
        result = self._call(_orca_swap_bytes())
        assert result.instruction_type == "swap"

    def test_swap_amount_decoded(self):
        """Swap amount is decoded correctly."""
        result = self._call(_orca_swap_bytes(amount=1000))
        assert result.decoded_args["amount"] == 1000

    def test_swap_a_to_b_true(self):
        """a_to_b flag decoded as True when set."""
        result = self._call(_orca_swap_bytes(a_to_b=True))
        assert result.decoded_args["a_to_b"] is True

    def test_swap_a_to_b_false(self):
        """a_to_b flag decoded as False when clear."""
        result = self._call(_orca_swap_bytes(a_to_b=False))
        assert result.decoded_args["a_to_b"] is False

    def test_swap_threshold_decoded(self):
        """other_amount_threshold decoded correctly."""
        result = self._call(_orca_swap_bytes(amount=5000, threshold=4900))
        assert result.decoded_args["other_amount_threshold"] == 4900

    def test_two_hop_swap_instruction_type(self):
        """twoHopSwap discriminant produces instruction_type=='twoHopSwap'."""
        result = self._call(_orca_two_hop_bytes(amount=2000))
        assert result.instruction_type == "twoHopSwap"

    def test_two_hop_swap_ok_status(self):
        """twoHopSwap with sufficient bytes produces decode_status=='ok'."""
        result = self._call(_orca_two_hop_bytes())
        assert result.decode_status == "ok"

    def test_two_hop_swap_amount_decoded(self):
        """twoHopSwap amount is decoded correctly."""
        result = self._call(_orca_two_hop_bytes(amount=9999))
        assert result.decoded_args["amount"] == 9999

    def test_too_short_data_returns_raw(self):
        """Data shorter than 8 bytes falls back to decode_status=='raw'."""
        result = self._call(b"\x00" * 4)
        assert result.decode_status == "raw"

    def test_empty_data_returns_raw(self):
        """Empty data falls back to decode_status=='raw'."""
        result = self._call(b"")
        assert result.decode_status == "raw"

    def test_program_name_preserved(self):
        """program_name is always 'orca_whirlpool' regardless of data."""
        result = self._call(b"")
        assert result.program_name == "orca_whirlpool"


# ---------------------------------------------------------------------------
# Byte builder for Meteora DLMM
# ---------------------------------------------------------------------------


def _meteora_swap_bytes(amount_in: int = 5000, min_out: int = 4900) -> bytes:
    """Build valid Meteora DLMM swap instruction bytes."""
    disc = _disc("swap")
    return (
        disc
        + struct.pack("<Q", amount_in)  # amount_in u64
        + struct.pack("<Q", min_out)    # min_amount_out u64
    )  # total: 8 + 8 + 8 = 24 bytes


# ---------------------------------------------------------------------------
# _decode_meteora_dlmm tests
# ---------------------------------------------------------------------------


class TestDecodeMeteoraDLMM:
    """Tests for SolanaInstructionParser._decode_meteora_dlmm."""

    def setup_method(self):
        self.parser = SolanaInstructionParser()
        self.program_id = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB"
        self.accounts = [f"acc{i}" for i in range(15)]

    def _call(self, data: bytes) -> ParsedInstruction:
        return self.parser._decode_meteora_dlmm(
            self.program_id, self.accounts, data, ""
        )

    def test_swap_ok_status(self):
        """Valid swap bytes produce decode_status=='ok'."""
        result = self._call(_meteora_swap_bytes())
        assert result.decode_status == "ok"

    def test_swap_instruction_type(self):
        """Valid swap bytes produce instruction_type=='swap'."""
        result = self._call(_meteora_swap_bytes())
        assert result.instruction_type == "swap"

    def test_swap_amount_in_decoded(self):
        """amount_in is decoded correctly from swap bytes."""
        result = self._call(_meteora_swap_bytes(amount_in=5000))
        assert result.decoded_args["amount_in"] == 5000

    def test_swap_min_amount_out_decoded(self):
        """min_amount_out is decoded correctly."""
        result = self._call(_meteora_swap_bytes(amount_in=5000, min_out=4800))
        assert result.decoded_args["min_amount_out"] == 4800

    def test_wrong_discriminant_partial_status(self):
        """Unrecognised discriminant produces decode_status=='partial' (unknown_meteora)."""
        # Use a discriminant that is definitely not 'swap'
        bad_disc = _disc("initialize")
        data = bad_disc + struct.pack("<Q", 100) + struct.pack("<Q", 90)
        result = self._call(data)
        assert result.instruction_type == "unknown_meteora"
        assert result.decode_status == "partial"

    def test_too_short_data_returns_raw(self):
        """Data shorter than 8 bytes falls back to decode_status=='raw'."""
        result = self._call(b"\x00" * 5)
        assert result.decode_status == "raw"

    def test_program_name_preserved(self):
        """program_name is always 'meteora_dlmm'."""
        result = self._call(b"")
        assert result.program_name == "meteora_dlmm"


# ---------------------------------------------------------------------------
# Byte builder for Phoenix
# ---------------------------------------------------------------------------


def _phoenix_swap_bytes(
    discriminant: int = 9,
    side: int = 0,
    num_base_lots: int = 100,
    min_base_lots_out: int = 95,
    num_quote_lots: int = 50,
    min_quote_lots_out: int = 45,
) -> bytes:
    """Build a Phoenix swap instruction byte sequence."""
    return (
        bytes([discriminant, side])
        + struct.pack("<Q", num_base_lots)
        + struct.pack("<Q", min_base_lots_out)
        + struct.pack("<Q", num_quote_lots)
        + struct.pack("<Q", min_quote_lots_out)
    )  # 1 + 1 + 8 + 8 + 8 + 8 = 34 bytes


# ---------------------------------------------------------------------------
# _decode_phoenix tests
# ---------------------------------------------------------------------------


class TestDecodePhoenix:
    """Tests for SolanaInstructionParser._decode_phoenix."""

    def setup_method(self):
        self.parser = SolanaInstructionParser()
        self.program_id = "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY"
        self.accounts = [f"acc{i}" for i in range(10)]

    def _call(self, data: bytes) -> ParsedInstruction:
        return self.parser._decode_phoenix(
            self.program_id, self.accounts, data, ""
        )

    def test_swap_ok_status(self):
        """Discriminant 9 with sufficient data → decode_status=='ok'."""
        result = self._call(_phoenix_swap_bytes(discriminant=9))
        assert result.decode_status == "ok"

    def test_swap_instruction_type(self):
        """Discriminant 9 → instruction_type=='swap'."""
        result = self._call(_phoenix_swap_bytes(discriminant=9))
        assert result.instruction_type == "swap"

    def test_swap_side_bid(self):
        """Side byte 0 → 'bid'."""
        result = self._call(_phoenix_swap_bytes(discriminant=9, side=0))
        assert result.decoded_args["side"] == "bid"

    def test_swap_side_ask(self):
        """Side byte 1 → 'ask'."""
        result = self._call(_phoenix_swap_bytes(discriminant=9, side=1))
        assert result.decoded_args["side"] == "ask"

    def test_swap_num_base_lots_decoded(self):
        """num_base_lots decoded correctly."""
        result = self._call(_phoenix_swap_bytes(discriminant=9, num_base_lots=100))
        assert result.decoded_args["num_base_lots"] == 100

    def test_swap_min_base_lots_out_decoded(self):
        """min_base_lots_out decoded correctly."""
        result = self._call(_phoenix_swap_bytes(discriminant=9, min_base_lots_out=95))
        assert result.decoded_args["min_base_lots_out"] == 95

    def test_swap_num_quote_lots_decoded(self):
        """num_quote_lots decoded correctly."""
        result = self._call(_phoenix_swap_bytes(discriminant=9, num_quote_lots=50))
        assert result.decoded_args["num_quote_lots"] == 50

    def test_swap_with_free_funds(self):
        """Discriminant 10 → instruction_type=='swapWithFreeFunds'."""
        result = self._call(_phoenix_swap_bytes(discriminant=10))
        assert result.instruction_type == "swapWithFreeFunds"
        assert result.decode_status == "ok"

    def test_unknown_discriminant_partial(self):
        """Discriminant not in _PHOENIX_INSTRUCTIONS → 'unknown_phoenix', 'partial'."""
        data = _phoenix_swap_bytes(discriminant=0)
        result = self._call(data)
        assert result.instruction_type == "unknown_phoenix"
        assert result.decode_status == "partial"

    def test_empty_data_raw(self):
        """Empty data → decode_status=='raw'."""
        result = self._call(b"")
        assert result.decode_status == "raw"

    def test_program_name_preserved(self):
        """program_name is always 'phoenix'."""
        result = self._call(b"")
        assert result.program_name == "phoenix"


# ---------------------------------------------------------------------------
# Byte builder for OpenBook v2
# ---------------------------------------------------------------------------


def _openbook_place_take_bytes(side: int = 0, max_base_lots: int = 500, max_quote_lots: int = 10000) -> bytes:
    """Build an OpenBook v2 placeTakeOrder instruction byte sequence.

    Layout: discriminant(8) + side(1) + max_base_lots(i64,8)
            + max_quote_lots_including_fees(i64,8) + limit(1) = 26 bytes.
    """
    disc = _disc("place_take_order")
    return (
        disc
        + bytes([side])
        + struct.pack("<q", max_base_lots)
        + struct.pack("<q", max_quote_lots)
        + bytes([10])  # limit
    )  # 8 + 1 + 8 + 8 + 1 = 26 bytes


# ---------------------------------------------------------------------------
# _decode_openbook_v2 tests
# ---------------------------------------------------------------------------


class TestDecodeOpenbookV2:
    """Tests for SolanaInstructionParser._decode_openbook_v2."""

    def setup_method(self):
        self.parser = SolanaInstructionParser()
        self.program_id = "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb"
        self.accounts = [f"acc{i}" for i in range(15)]

    def _call(self, data: bytes) -> ParsedInstruction:
        return self.parser._decode_openbook_v2(
            self.program_id, self.accounts, data, ""
        )

    def test_place_take_order_ok_status(self):
        """Valid placeTakeOrder bytes → decode_status=='ok'."""
        result = self._call(_openbook_place_take_bytes())
        assert result.decode_status == "ok"

    def test_place_take_order_instruction_type(self):
        """Valid placeTakeOrder bytes → instruction_type=='placeTakeOrder'."""
        result = self._call(_openbook_place_take_bytes())
        assert result.instruction_type == "placeTakeOrder"

    def test_place_take_order_side_bid(self):
        """Side byte 0 → 'bid'."""
        result = self._call(_openbook_place_take_bytes(side=0))
        assert result.decoded_args["side"] == "bid"

    def test_place_take_order_side_ask(self):
        """Side byte 1 → 'ask'."""
        result = self._call(_openbook_place_take_bytes(side=1))
        assert result.decoded_args["side"] == "ask"

    def test_place_take_order_max_base_lots(self):
        """max_base_lots decoded correctly."""
        result = self._call(_openbook_place_take_bytes(max_base_lots=500))
        assert result.decoded_args["max_base_lots"] == 500

    def test_place_take_order_max_quote_lots(self):
        """max_quote_lots_including_fees decoded correctly."""
        result = self._call(_openbook_place_take_bytes(max_quote_lots=10000))
        assert result.decoded_args["max_quote_lots_including_fees"] == 10000

    def test_unknown_discriminant_partial(self):
        """Unknown discriminant → 'unknown_openbook_v2', decode_status still 'partial'."""
        # Use a discriminant for a name that OpenBook doesn't handle
        bad_disc = _disc("initialize")
        data = bad_disc + bytes([0]) + struct.pack("<q", 100) + struct.pack("<q", 1000) + bytes([5])
        result = self._call(data)
        assert result.instruction_type == "unknown_openbook_v2"
        assert result.decode_status == "partial"

    def test_too_short_data_returns_raw(self):
        """Data shorter than 8 bytes falls back to decode_status=='raw'."""
        result = self._call(b"\x00" * 4)
        assert result.decode_status == "raw"

    def test_program_name_preserved(self):
        """program_name is always 'openbook_v2'."""
        result = self._call(b"")
        assert result.program_name == "openbook_v2"


# ---------------------------------------------------------------------------
# parse_transaction_instructions dispatch tests for new programs
# ---------------------------------------------------------------------------


def _ix_dict(program_id: str, account_keys: List[str], data_bytes: bytes) -> dict:
    """Build a minimal instruction dict for parse_transaction_instructions."""
    prog_idx = 0
    return {
        "programIdIndex": prog_idx,
        "accounts": list(range(1, len(account_keys))),
        "data": base64.b64encode(data_bytes).decode(),
    }


class TestDispatchNewPrograms:
    """Tests that parse_transaction_instructions routes to the correct decoder."""

    def setup_method(self):
        self.parser = SolanaInstructionParser()

    def _parse_single(self, program_id: str, data_bytes: bytes) -> ParsedInstruction:
        """Parse a single fake instruction with the given program_id."""
        account_keys = [program_id] + [f"acc{i}" for i in range(12)]
        ix = _ix_dict(program_id, account_keys, data_bytes)
        results = self.parser.parse_transaction_instructions([ix], account_keys)
        assert len(results) == 1
        return results[0]

    def test_orca_primary_dispatches_correctly(self):
        """Primary Orca Whirlpool program_id → program_name=='orca_whirlpool'."""
        result = self._parse_single(
            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
            _orca_swap_bytes(),
        )
        assert result.program_name == "orca_whirlpool"

    def test_orca_primary_swap_decoded(self):
        """Primary Orca program produces a fully decoded swap."""
        result = self._parse_single(
            "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
            _orca_swap_bytes(amount=1234),
        )
        assert result.instruction_type == "swap"
        assert result.decode_status == "ok"
        assert result.decoded_args["amount"] == 1234

    def test_orca_secondary_dispatches_correctly(self):
        """Secondary Orca Whirlpool program_id → program_name=='orca_whirlpool'."""
        result = self._parse_single(
            "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",
            _orca_swap_bytes(),
        )
        assert result.program_name == "orca_whirlpool"
        assert result.instruction_type == "swap"

    def test_meteora_dispatches_correctly(self):
        """Meteora DLMM program_id → program_name=='meteora_dlmm' with correct decode."""
        result = self._parse_single(
            "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",
            _meteora_swap_bytes(amount_in=7777),
        )
        assert result.program_name == "meteora_dlmm"
        assert result.instruction_type == "swap"
        assert result.decode_status == "ok"
        assert result.decoded_args["amount_in"] == 7777

    def test_phoenix_dispatches_correctly(self):
        """Phoenix program_id → program_name=='phoenix' with correct decode."""
        result = self._parse_single(
            "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY",
            _phoenix_swap_bytes(discriminant=9, num_base_lots=200),
        )
        assert result.program_name == "phoenix"
        assert result.instruction_type == "swap"
        assert result.decode_status == "ok"
        assert result.decoded_args["num_base_lots"] == 200

    def test_openbook_v2_dispatches_correctly(self):
        """OpenBook v2 program_id → program_name=='openbook_v2' with correct decode."""
        result = self._parse_single(
            "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb",
            _openbook_place_take_bytes(max_base_lots=999),
        )
        assert result.program_name == "openbook_v2"
        assert result.instruction_type == "placeTakeOrder"
        assert result.decode_status == "ok"
        assert result.decoded_args["max_base_lots"] == 999
