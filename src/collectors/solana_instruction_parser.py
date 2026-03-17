"""Solana instruction-level parser for investigation-grade transaction analysis.

Decodes raw Solana instructions for the programs most relevant to compliance:

* **SPL Token Program** — ``transfer`` / ``transferChecked`` / ``mintTo`` /
  ``burn``
* **Jupiter Aggregator v6** — ``route`` (aggregated swap)
* **Raydium AMM v4** — ``swapBaseIn`` / ``swapBaseOut``
* **Wormhole Token Bridge** — ``transferTokens`` (cross-chain bridge)
* **System Program** — ``transfer`` (native SOL)

Unrecognised program instructions are returned with ``instruction_type:
"unknown"`` so callers never receive ``None``.

Design notes
------------
All decoding is deliberately defensive — Borsh/base64 parse errors fall
through to a raw-bytes representation so a single malformed instruction
cannot drop an entire transaction.  Callers should check
``decode_status`` ("ok" | "partial" | "raw") before trusting field values.
"""

from __future__ import annotations

import base64
import logging
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known program IDs
# ---------------------------------------------------------------------------

#: Mapping from program_id (base58 string) → human-readable name.
KNOWN_PROGRAMS: Dict[str, str] = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "spl_token",
    "Token2022Fg6u6R98E8RYgKgwFG4L8iYUPQiCT9gLjAF": "spl_token_2022",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter_v6",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium_amm_v4",
    "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth": "wormhole_token_bridge",
    "11111111111111111111111111111111": "system_program",
}

# ---------------------------------------------------------------------------
# SPL Token instruction discriminants (first byte of instruction data)
# ---------------------------------------------------------------------------

_SPL_TOKEN_INSTRUCTIONS: Dict[int, str] = {
    0: "initializeMint",
    1: "initializeAccount",
    3: "transfer",
    7: "mintTo",
    8: "burn",
    9: "closeAccount",
    12: "transferChecked",
    14: "mintToChecked",
    15: "burnChecked",
}

# System Program instruction types (u32 little-endian at offset 0)
_SYSTEM_INSTRUCTIONS: Dict[int, str] = {
    0: "createAccount",
    2: "transfer",
    3: "createAccountWithSeed",
    11: "transferWithSeed",
}

# Raydium AMM instruction discriminants (first byte)
_RAYDIUM_INSTRUCTIONS: Dict[int, str] = {
    9: "swapBaseIn",
    11: "swapBaseOut",
}

# Wormhole Token Bridge instruction discriminants (first byte)
_WORMHOLE_INSTRUCTIONS: Dict[int, str] = {
    1: "initialize",
    2: "transferTokens",
    4: "redeemTokens",
    10: "transferTokensWithPayload",
}


# ---------------------------------------------------------------------------
# Parsed instruction dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedInstruction:
    """Decoded representation of a single Solana instruction.

    Attributes:
        program_id: The program account key (base58 string).
        program_name: Human-readable program name, or ``"unknown"`` if not in
            ``KNOWN_PROGRAMS``.
        instruction_type: Decoded instruction variant name (e.g.
            ``"transfer"``, ``"swapBaseIn"``).  ``"unknown"`` when the
            discriminant is not recognised.
        accounts: List of account public keys referenced by the instruction in
            their positional order.
        decoded_args: Key-value pairs extracted from the instruction data
            (amounts, mints, etc.).  Empty dict when decoding fails entirely.
        raw_data_b64: Base64-encoded raw instruction data for audit purposes.
        decode_status: One of ``"ok"`` (full decode), ``"partial"`` (only the
            discriminant was decoded), ``"raw"`` (no decode possible).
    """

    program_id: str
    program_name: str
    instruction_type: str
    accounts: List[str]
    decoded_args: Dict[str, Any] = field(default_factory=dict)
    raw_data_b64: str = ""
    decode_status: str = "ok"  # ok | partial | raw


# ---------------------------------------------------------------------------
# Main parser class
# ---------------------------------------------------------------------------


class SolanaInstructionParser:
    """Parse Solana transaction instructions into investigation-ready dicts.

    Instantiate once and reuse — the class has no mutable state.

    Example::

        parser = SolanaInstructionParser()
        instructions = parser.parse_transaction_instructions(
            instructions=tx["message"]["instructions"],
            inner_instructions=meta.get("innerInstructions", []),
            account_keys=tx["message"]["accountKeys"],
        )
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_transaction_instructions(
        self,
        instructions: List[Dict[str, Any]],
        account_keys: List[str],
        inner_instructions: Optional[List[Dict[str, Any]]] = None,
    ) -> List[ParsedInstruction]:
        """Parse all top-level and inner instructions of a transaction.

        Args:
            instructions: ``message.instructions`` list from a Solana RPC
                ``getTransaction`` response.
            account_keys: ``message.accountKeys`` list from the same response.
            inner_instructions: Optional ``meta.innerInstructions`` list;
                inner instructions are appended after their parent.

        Returns:
            Ordered list of :class:`ParsedInstruction` — one per instruction
            (outer and inner combined).
        """
        results: List[ParsedInstruction] = []

        for outer_idx, ix in enumerate(instructions):
            parsed = self._parse_one(ix, account_keys)
            results.append(parsed)

            # Append matching inner instructions immediately after their parent
            if inner_instructions:
                for inner_group in inner_instructions:
                    if inner_group.get("index") == outer_idx:
                        for inner_ix in inner_group.get("instructions", []):
                            results.append(self._parse_one(inner_ix, account_keys))

        return results

    def to_node_dict(
        self,
        ix: ParsedInstruction,
        *,
        branch_id: str,
        depth: int,
        parent_node_id: str,
        path_id: str,
        ix_index: int,
    ) -> Dict[str, Any]:
        """Serialise a :class:`ParsedInstruction` to an ``InstructionNode`` dict.

        The returned dict is suitable for inclusion in an
        ``ExpansionResponse.new_nodes`` list.

        Args:
            ix: The parsed instruction.
            branch_id: Lineage branch identifier.
            depth: Graph insertion depth.
            parent_node_id: ID of the parent node (usually the tx node).
            path_id: Path identifier for this expansion branch.
            ix_index: Zero-based instruction index within the transaction.

        Returns:
            Dict with all lineage fields and instruction-specific fields.
        """
        node_id = f"solana:ix:{parent_node_id}:{ix_index}"
        return {
            "node_id": node_id,
            "node_type": "instruction",
            "program_id": ix.program_id,
            "program_name": ix.program_name,
            "instruction_type": ix.instruction_type,
            "accounts": ix.accounts,
            "decoded_args": ix.decoded_args,
            "raw_data_b64": ix.raw_data_b64,
            "decode_status": ix.decode_status,
            "depth": depth,
            "parent_id": parent_node_id,
            "branch_id": branch_id,
            "path_id": path_id,
        }

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _parse_one(
        self, ix: Dict[str, Any], account_keys: List[str]
    ) -> ParsedInstruction:
        """Dispatch a single raw instruction to the correct decoder.

        Args:
            ix: Raw instruction dict (``programIdIndex``, ``accounts``,
                ``data``).
            account_keys: Full list of account keys for the transaction.

        Returns:
            :class:`ParsedInstruction` — never raises.
        """
        program_index = ix.get("programIdIndex")
        program_id = (
            account_keys[program_index]
            if program_index is not None and program_index < len(account_keys)
            else "unknown"
        )
        program_name = KNOWN_PROGRAMS.get(program_id, "unknown")

        # Resolve account indices to actual pubkeys
        account_indices = ix.get("accounts", [])
        accounts = [
            account_keys[i]
            for i in account_indices
            if isinstance(i, int) and i < len(account_keys)
        ]

        raw_data_b64 = ix.get("data", "")
        raw_bytes = _decode_data(raw_data_b64)

        try:
            if program_name in ("spl_token", "spl_token_2022"):
                return self._decode_spl_token(
                    program_id, program_name, accounts, raw_bytes, raw_data_b64
                )
            if program_name == "jupiter_v6":
                return self._decode_jupiter(
                    program_id, accounts, raw_bytes, raw_data_b64
                )
            if program_name == "raydium_amm_v4":
                return self._decode_raydium(
                    program_id, accounts, raw_bytes, raw_data_b64
                )
            if program_name == "wormhole_token_bridge":
                return self._decode_wormhole(
                    program_id, accounts, raw_bytes, raw_data_b64
                )
            if program_name == "system_program":
                return self._decode_system(
                    program_id, accounts, raw_bytes, raw_data_b64
                )
        except Exception as exc:
            logger.debug(
                f"Instruction decode failed for {program_name}: {exc}"
            )

        # Fallback — unknown or decode-failed
        return ParsedInstruction(
            program_id=program_id,
            program_name=program_name,
            instruction_type="unknown",
            accounts=accounts,
            raw_data_b64=raw_data_b64,
            decode_status="raw",
        )

    # ------------------------------------------------------------------
    # SPL Token decoder
    # ------------------------------------------------------------------

    def _decode_spl_token(
        self,
        program_id: str,
        program_name: str,
        accounts: List[str],
        data: bytes,
        raw_b64: str,
    ) -> ParsedInstruction:
        """Decode SPL Token Program instructions.

        Layout (simplified Borsh):
        * Byte 0: instruction discriminant
        * Bytes 1+: instruction-specific fields

        Args:
            program_id: SPL token program ID.
            program_name: ``"spl_token"`` or ``"spl_token_2022"``.
            accounts: Resolved account pubkeys.
            data: Raw instruction bytes.
            raw_b64: Base64 source (stored for audit).

        Returns:
            :class:`ParsedInstruction` with decoded fields.
        """
        if not data:
            return ParsedInstruction(
                program_id=program_id,
                program_name=program_name,
                instruction_type="unknown",
                accounts=accounts,
                raw_data_b64=raw_b64,
                decode_status="raw",
            )

        discriminant = data[0]
        ix_name = _SPL_TOKEN_INSTRUCTIONS.get(discriminant, "unknown")
        decoded_args: Dict[str, Any] = {}
        status = "partial"

        if ix_name == "transfer" and len(data) >= 9:
            # u8 discriminant + u64 amount (little-endian)
            amount = struct.unpack_from("<Q", data, 1)[0]
            decoded_args = {
                "amount": amount,
                "source_token_account": accounts[0] if len(accounts) > 0 else None,
                "destination_token_account": accounts[1] if len(accounts) > 1 else None,
                "owner": accounts[2] if len(accounts) > 2 else None,
            }
            status = "ok"

        elif ix_name == "transferChecked" and len(data) >= 10:
            # u8 discriminant + u64 amount + u8 decimals
            amount = struct.unpack_from("<Q", data, 1)[0]
            decimals = data[9]
            decoded_args = {
                "amount": amount,
                "decimals": decimals,
                "source_token_account": accounts[0] if len(accounts) > 0 else None,
                "mint": accounts[1] if len(accounts) > 1 else None,
                "destination_token_account": accounts[2] if len(accounts) > 2 else None,
                "owner": accounts[3] if len(accounts) > 3 else None,
            }
            status = "ok"

        elif ix_name in ("mintTo", "mintToChecked") and len(data) >= 9:
            amount = struct.unpack_from("<Q", data, 1)[0]
            decoded_args = {
                "amount": amount,
                "mint": accounts[0] if len(accounts) > 0 else None,
                "destination_token_account": accounts[1] if len(accounts) > 1 else None,
                "mint_authority": accounts[2] if len(accounts) > 2 else None,
            }
            status = "ok"

        elif ix_name in ("burn", "burnChecked") and len(data) >= 9:
            amount = struct.unpack_from("<Q", data, 1)[0]
            decoded_args = {
                "amount": amount,
                "source_token_account": accounts[0] if len(accounts) > 0 else None,
                "mint": accounts[1] if len(accounts) > 1 else None,
                "owner": accounts[2] if len(accounts) > 2 else None,
            }
            status = "ok"

        return ParsedInstruction(
            program_id=program_id,
            program_name=program_name,
            instruction_type=ix_name,
            accounts=accounts,
            decoded_args=decoded_args,
            raw_data_b64=raw_b64,
            decode_status=status,
        )

    # ------------------------------------------------------------------
    # System Program decoder
    # ------------------------------------------------------------------

    def _decode_system(
        self,
        program_id: str,
        accounts: List[str],
        data: bytes,
        raw_b64: str,
    ) -> ParsedInstruction:
        """Decode System Program instructions (native SOL transfers).

        Layout: u32 instruction type + instruction-specific data.

        Args:
            program_id: System program ID.
            accounts: Resolved account pubkeys.
            data: Raw instruction bytes.
            raw_b64: Base64 source.

        Returns:
            :class:`ParsedInstruction` with decoded lamport amounts.
        """
        if len(data) < 4:
            return ParsedInstruction(
                program_id=program_id,
                program_name="system_program",
                instruction_type="unknown",
                accounts=accounts,
                raw_data_b64=raw_b64,
                decode_status="raw",
            )

        ix_type = struct.unpack_from("<I", data, 0)[0]
        ix_name = _SYSTEM_INSTRUCTIONS.get(ix_type, "unknown")
        decoded_args: Dict[str, Any] = {}
        status = "partial"

        if ix_name == "transfer" and len(data) >= 12:
            # u32 type + u64 lamports
            lamports = struct.unpack_from("<Q", data, 4)[0]
            decoded_args = {
                "lamports": lamports,
                "sol_amount": round(lamports / 1e9, 9),
                "from": accounts[0] if accounts else None,
                "to": accounts[1] if len(accounts) > 1 else None,
            }
            status = "ok"

        elif ix_name == "createAccount" and len(data) >= 28:
            lamports = struct.unpack_from("<Q", data, 4)[0]
            space = struct.unpack_from("<Q", data, 12)[0]
            decoded_args = {
                "lamports": lamports,
                "space": space,
                "from": accounts[0] if accounts else None,
                "new_account": accounts[1] if len(accounts) > 1 else None,
            }
            status = "ok"

        return ParsedInstruction(
            program_id=program_id,
            program_name="system_program",
            instruction_type=ix_name,
            accounts=accounts,
            decoded_args=decoded_args,
            raw_data_b64=raw_b64,
            decode_status=status,
        )

    # ------------------------------------------------------------------
    # Jupiter v6 decoder
    # ------------------------------------------------------------------

    def _decode_jupiter(
        self,
        program_id: str,
        accounts: List[str],
        data: bytes,
        raw_b64: str,
    ) -> ParsedInstruction:
        """Decode Jupiter Aggregator v6 instructions.

        Jupiter v6 uses an 8-byte Anchor discriminant (first 8 bytes of
        SHA-256("global:<instruction_name>")).  We match against the known
        ``route`` discriminant prefix; other variants fall back to partial.

        Args:
            program_id: Jupiter v6 program ID.
            accounts: Resolved account pubkeys.
            data: Raw instruction bytes.
            raw_b64: Base64 source.

        Returns:
            :class:`ParsedInstruction` with swap metadata where decodeable.
        """
        # Jupiter Anchor discriminant for `route`: first 8 bytes
        # sha256("global:route")[0:8] = e5 17 cb 97 7a e3 ad 2a
        ROUTE_DISCRIMINANT = bytes([0xE5, 0x17, 0xCB, 0x97, 0x7A, 0xE3, 0xAD, 0x2A])
        # sharedAccountsRoute discriminant: d3 23 a1 6e 46 0d fb 11
        SHARED_ROUTE_DISCRIMINANT = bytes([0xD3, 0x23, 0xA1, 0x6E, 0x46, 0x0D, 0xFB, 0x11])

        if len(data) < 8:
            return ParsedInstruction(
                program_id=program_id,
                program_name="jupiter_v6",
                instruction_type="unknown",
                accounts=accounts,
                raw_data_b64=raw_b64,
                decode_status="raw",
            )

        discriminant = data[:8]
        decoded_args: Dict[str, Any] = {}
        status = "partial"

        if discriminant == ROUTE_DISCRIMINANT:
            ix_name = "route"
            # After discriminant: in_amount (u64) + quoted_out_amount (u64)
            # + slippage_bps (u16) — positions 8, 16, 24
            if len(data) >= 26:
                in_amount = struct.unpack_from("<Q", data, 8)[0]
                quoted_out = struct.unpack_from("<Q", data, 16)[0]
                slippage_bps = struct.unpack_from("<H", data, 24)[0]
                decoded_args = {
                    "in_amount": in_amount,
                    "quoted_out_amount": quoted_out,
                    "slippage_bps": slippage_bps,
                    "input_token_account": accounts[3] if len(accounts) > 3 else None,
                    "output_token_account": accounts[6] if len(accounts) > 6 else None,
                    "user_transfer_authority": accounts[2] if len(accounts) > 2 else None,
                }
                status = "ok"
        elif discriminant == SHARED_ROUTE_DISCRIMINANT:
            ix_name = "sharedAccountsRoute"
            if len(data) >= 26:
                in_amount = struct.unpack_from("<Q", data, 8)[0]
                quoted_out = struct.unpack_from("<Q", data, 16)[0]
                decoded_args = {
                    "in_amount": in_amount,
                    "quoted_out_amount": quoted_out,
                    "input_mint": accounts[7] if len(accounts) > 7 else None,
                    "output_mint": accounts[8] if len(accounts) > 8 else None,
                }
                status = "ok"
        else:
            ix_name = "unknown_jupiter"
            status = "partial"

        return ParsedInstruction(
            program_id=program_id,
            program_name="jupiter_v6",
            instruction_type=ix_name,
            accounts=accounts,
            decoded_args=decoded_args,
            raw_data_b64=raw_b64,
            decode_status=status,
        )

    # ------------------------------------------------------------------
    # Raydium AMM v4 decoder
    # ------------------------------------------------------------------

    def _decode_raydium(
        self,
        program_id: str,
        accounts: List[str],
        data: bytes,
        raw_b64: str,
    ) -> ParsedInstruction:
        """Decode Raydium AMM v4 swap instructions.

        Layout:
        * Byte 0: instruction discriminant (9 = swapBaseIn, 11 = swapBaseOut)
        * Bytes 1-8: amount_in (u64)
        * Bytes 9-16: minimum_amount_out (u64)

        Args:
            program_id: Raydium AMM v4 program ID.
            accounts: Resolved account pubkeys.
            data: Raw instruction bytes.
            raw_b64: Base64 source.

        Returns:
            :class:`ParsedInstruction` with swap amounts.
        """
        if not data:
            return ParsedInstruction(
                program_id=program_id,
                program_name="raydium_amm_v4",
                instruction_type="unknown",
                accounts=accounts,
                raw_data_b64=raw_b64,
                decode_status="raw",
            )

        discriminant = data[0]
        ix_name = _RAYDIUM_INSTRUCTIONS.get(discriminant, "unknown_raydium")
        decoded_args: Dict[str, Any] = {}
        status = "partial"

        if ix_name in ("swapBaseIn", "swapBaseOut") and len(data) >= 17:
            amount_in = struct.unpack_from("<Q", data, 1)[0]
            min_amount_out = struct.unpack_from("<Q", data, 9)[0]
            decoded_args = {
                "amount_in": amount_in,
                "minimum_amount_out": min_amount_out,
                # Standard Raydium AMM v4 account layout
                "amm_pool": accounts[1] if len(accounts) > 1 else None,
                "user_source_token_account": accounts[15] if len(accounts) > 15 else None,
                "user_destination_token_account": accounts[16] if len(accounts) > 16 else None,
                "user_source_owner": accounts[17] if len(accounts) > 17 else None,
            }
            status = "ok"

        return ParsedInstruction(
            program_id=program_id,
            program_name="raydium_amm_v4",
            instruction_type=ix_name,
            accounts=accounts,
            decoded_args=decoded_args,
            raw_data_b64=raw_b64,
            decode_status=status,
        )

    # ------------------------------------------------------------------
    # Wormhole Token Bridge decoder
    # ------------------------------------------------------------------

    def _decode_wormhole(
        self,
        program_id: str,
        accounts: List[str],
        data: bytes,
        raw_b64: str,
    ) -> ParsedInstruction:
        """Decode Wormhole Token Bridge instructions.

        Layout:
        * Byte 0: instruction discriminant
        * Bytes 1-8: amount (u64)
        * Bytes 9-40: target_address (32 bytes — destination chain address)
        * Bytes 41-42: target_chain (u16 Wormhole chain ID)
        * Bytes 43: nonce (u8)

        Args:
            program_id: Wormhole token bridge program ID.
            accounts: Resolved account pubkeys.
            data: Raw instruction bytes.
            raw_b64: Base64 source.

        Returns:
            :class:`ParsedInstruction` with bridge transfer metadata.
        """
        if not data:
            return ParsedInstruction(
                program_id=program_id,
                program_name="wormhole_token_bridge",
                instruction_type="unknown",
                accounts=accounts,
                raw_data_b64=raw_b64,
                decode_status="raw",
            )

        discriminant = data[0]
        ix_name = _WORMHOLE_INSTRUCTIONS.get(discriminant, "unknown_wormhole")
        decoded_args: Dict[str, Any] = {}
        status = "partial"

        WORMHOLE_CHAIN_IDS: Dict[int, str] = {
            1: "solana",
            2: "ethereum",
            4: "bsc",
            5: "polygon",
            6: "avalanche",
            10: "fantom",
            23: "arbitrum",
            24: "optimism",
        }

        if ix_name in ("transferTokens", "transferTokensWithPayload") and len(data) >= 43:
            amount = struct.unpack_from("<Q", data, 1)[0]
            target_address_bytes = data[9:41]
            target_chain_id = struct.unpack_from("<H", data, 41)[0]
            decoded_args = {
                "amount": amount,
                "target_address_hex": target_address_bytes.hex(),
                "target_chain_id": target_chain_id,
                "target_chain_name": WORMHOLE_CHAIN_IDS.get(target_chain_id, "unknown"),
                "from_token_account": accounts[0] if accounts else None,
                "mint": accounts[1] if len(accounts) > 1 else None,
                "bridge_config": accounts[2] if len(accounts) > 2 else None,
            }
            status = "ok"

        return ParsedInstruction(
            program_id=program_id,
            program_name="wormhole_token_bridge",
            instruction_type=ix_name,
            accounts=accounts,
            decoded_args=decoded_args,
            raw_data_b64=raw_b64,
            decode_status=status,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_data(data: Any) -> bytes:
    """Decode instruction data to bytes.

    Solana RPC returns instruction data as base58 or base64 strings depending
    on the encoding requested.  We request ``encoding="json"`` which gives
    base58.  ``encoding="jsonParsed"`` gives base64.  We try base64 first
    (the common case in our pipeline) then fall back to base58.

    Args:
        data: Raw data field from the instruction dict.

    Returns:
        Decoded bytes, or empty bytes on failure.
    """
    if not data or not isinstance(data, str):
        return b""
    # Try base64 first
    try:
        # Standard base64 — pad if needed
        padded = data + "=" * (-len(data) % 4)
        return base64.b64decode(padded)
    except Exception:
        pass
    # Try base58
    try:
        import base58 as _b58  # type: ignore[import]

        return _b58.b58decode(data)
    except Exception:
        pass
    return b""
