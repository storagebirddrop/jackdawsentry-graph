"""
Jackdaw Sentry — Bridge Protocol Registry
Structured definitions for all 15 supported cross-chain bridge protocols.

Each ``BridgeProtocol`` record describes:
- The protocol's on-chain contract addresses per chain (for ingress detection)
- The API endpoints used for cross-chain correlation lookups
- The bridge mechanism (determines how to correlate ingress ↔ egress)
- A memo/calldata regex pattern (where applicable) for THORChain-style memos
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional


@dataclass
class BridgeProtocol:
    """Structured definition of a cross-chain bridge protocol."""

    protocol_id: str       # Stable snake_case identifier
    display_name: str
    # Mechanism determines how ingress ↔ egress are correlated:
    #   native_amm   — THORChain / Chainflip: memo on source tx links both sides
    #   lock_mint    — Wormhole, Allbridge: lock on source chain, mint on destination
    #   burn_release — Celer, Synapse: burn on source chain, release on destination
    #   solver       — deBridge, Mayan, Squid, LI.FI, Rango, Relay: off-chain solver fills
    #   liquidity    — Stargate, Across: AMM liquidity pools on both sides
    mechanism: str
    supported_chains: List[str]
    # Contract addresses per chain — used to detect bridge ingress in on-chain data.
    known_contract_addresses: Dict[str, List[str]] = field(default_factory=dict)
    # Public API base URL for correlation lookups (None = no public API available).
    api_base: Optional[str] = None
    # Path to the quote/status endpoint (appended to api_base).
    quote_endpoint: Optional[str] = None
    status_endpoint: Optional[str] = None
    # Regex pattern matching the memo / calldata field on source-chain txs.
    memo_pattern: Optional[str] = None
    # Regex pattern to identify protocol-issued deposit addresses.
    deposit_address_pattern: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry of all 15 required bridge protocols
# ---------------------------------------------------------------------------

BRIDGE_REGISTRY: Dict[str, BridgeProtocol] = {
    "thorchain": BridgeProtocol(
        protocol_id="thorchain",
        display_name="THORChain",
        mechanism="native_amm",
        api_base="https://thornode.ninerealms.com",
        quote_endpoint="/thorchain/quote/swap",
        status_endpoint="/thorchain/tx/details/{tx_id}",
        supported_chains=[
            "bitcoin", "ethereum", "bsc", "avalanche", "cosmos",
            "litecoin", "bitcoin_cash", "dogecoin",
        ],
        known_contract_addresses={
            "ethereum": ["0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146"],
            "bsc": ["0x8f92e7353b180937895e0c5937d616e8ea1a2bb9"],
            "avalanche": ["0x8f92e7353b180937895e0c5937d616e8ea1a2bb9"],
        },
        # THORChain memos encode the full swap intent:
        # SWAP:ETH.ETH:0xrecipient:minout OR ADD:BTC.BTC:addr
        memo_pattern=r"^(SWAP|ADD|WITHDRAW|REFUND|BOND|UNBOND|LEAVE|RESERVE|DONATE|NOOP|OUT|POOL):",
    ),

    "chainflip": BridgeProtocol(
        protocol_id="chainflip",
        display_name="Chainflip",
        mechanism="native_amm",
        api_base="https://chainflip-broker.io",
        quote_endpoint="/quote",
        status_endpoint="/swap/{swap_id}",
        supported_chains=["bitcoin", "ethereum", "polkadot", "arbitrum", "solana"],
        known_contract_addresses={
            "ethereum": [
                "0x6995ab7c4d7f4b03f467cf4c8e920427d9621dbd",
                "0xf5e10380213880111522dd0efd3dbb45b9f62bcc",
            ],
        },
        memo_pattern=r"^x:",  # Chainflip uses calldata with x: prefix
    ),

    "wormhole": BridgeProtocol(
        protocol_id="wormhole",
        display_name="Wormhole",
        mechanism="lock_mint",
        api_base="https://api.wormholescan.io",
        quote_endpoint="/api/v1/vaas",
        status_endpoint="/api/v1/operations?txHash={tx_hash}",
        supported_chains=[
            "ethereum", "solana", "bsc", "polygon", "avalanche", "fantom",
            "celo", "moonbeam", "arbitrum", "optimism", "base", "aptos", "sui",
        ],
        known_contract_addresses={
            "ethereum": ["0x3ee18b2214aff97000d974cf647e7c347e8fa585"],
            "solana": ["worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth"],
            "bsc": ["0xb6f6d86a8f9879a9c87f18830f2de421a59fe272"],
            "polygon": ["0x5a58505a96d1dbf8df91cb21b54419fc36e93fde"],
            "avalanche": ["0x0e082f06ff657d94310cb8ce8b0d9a04541d8052"],
            "arbitrum": ["0x0b2402144bb366a632d14b83f244d2e0e21bd39c"],
            "optimism": ["0x1d68124e65fafc907325e3edbf8c4d84499daa8b"],
            "base": ["0xbebdb6c8ddcc51dd1755b966d58bba425dfb0f7c"],
        },
    ),

    "debridge": BridgeProtocol(
        protocol_id="debridge",
        display_name="deBridge",
        mechanism="solver",
        api_base="https://api.dln.trade",
        quote_endpoint="/v1.0/dln/order/quote",
        status_endpoint="/v1.0/dln/order/{order_id}",
        supported_chains=[
            "ethereum", "bsc", "polygon", "arbitrum", "avalanche",
            "base", "solana", "linea",
        ],
        known_contract_addresses={
            "ethereum": ["0xeF4fB24aD0916217251F553c0596F8Edc630EB66"],
            "arbitrum": ["0xeF4fB24aD0916217251F553c0596F8Edc630EB66"],
            "bsc": ["0xeF4fB24aD0916217251F553c0596F8Edc630EB66"],
            "solana": ["src5qyZHqTqecJV4aY6Cb6zDZLMDzrDKKezs22Sf6Ax"],
        },
    ),

    "mayan": BridgeProtocol(
        protocol_id="mayan",
        display_name="Mayan Finance",
        mechanism="solver",
        api_base="https://price-api.mayan.finance",
        quote_endpoint="/v3/quote",
        status_endpoint="/v3/swap/trx/{signature}",
        supported_chains=["ethereum", "solana", "bsc", "polygon", "avalanche", "arbitrum", "base"],
        known_contract_addresses={
            "ethereum": ["0x1aD5cb2955940F998081c1eF5f5F00875431aA90"],
            "solana": ["MayanU2yS5r3fUBoPRKmHtCm9e4mNR7TXbmvZs2KN3k"],
            "bsc": ["0x1aD5cb2955940F998081c1eF5f5F00875431aA90"],
        },
    ),

    "squid": BridgeProtocol(
        protocol_id="squid",
        display_name="Squid Router",
        mechanism="solver",
        api_base="https://apiplus.squidrouter.com",
        quote_endpoint="/v2/route",
        status_endpoint="/v2/status?transactionId={tx_hash}",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "base", "polygon", "avalanche",
            "bsc", "linea", "mantle", "scroll", "celo",
        ],
        known_contract_addresses={
            "ethereum": ["0xce16f69375520ab01377ce7b88f5ba8c48f8d666"],
            "arbitrum": ["0xce16f69375520ab01377ce7b88f5ba8c48f8d666"],
            "polygon": ["0xce16f69375520ab01377ce7b88f5ba8c48f8d666"],
        },
    ),

    "lifi": BridgeProtocol(
        protocol_id="lifi",
        display_name="LI.FI",
        mechanism="solver",
        api_base="https://li.quest/v1",
        quote_endpoint="/quote",
        status_endpoint="/status?txHash={tx_hash}",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "base", "polygon", "avalanche",
            "bsc", "gnosis", "fantom", "celo", "moonbeam", "linea",
        ],
        known_contract_addresses={
            "ethereum": ["0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"],
            "arbitrum": ["0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"],
            "polygon": ["0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"],
            "bsc": ["0x1231deb6f5749ef6ce6943a275a1d3e7486f4eae"],
        },
    ),

    "across": BridgeProtocol(
        protocol_id="across",
        display_name="Across Protocol",
        mechanism="liquidity",
        api_base="https://across.to/api",
        quote_endpoint="/suggested-fees",
        status_endpoint="/deposit/status?depositId={deposit_id}",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "base", "polygon", "linea",
            "scroll", "zksync", "mode",
        ],
        known_contract_addresses={
            "ethereum": ["0x5c7bcd6e7de5423a257d81b4f24d0a0b28f94a05"],
            "arbitrum": ["0xe35e9842fceaca96570b734083f4a58e8f7c5f2a"],
            "optimism": ["0x6f26bf09b1c792e3228e5467807a900a503c0281"],
            "base": ["0x09aea4b2242abc8bb4bb78d537a67a245a7bec64"],
            "polygon": ["0x9295ee1d8c5b022be115a2ad3c30c72e34e7f096"],
        },
    ),

    "celer": BridgeProtocol(
        protocol_id="celer",
        display_name="Celer cBridge",
        mechanism="burn_release",
        api_base="https://cbridge-prod2.celer.app",
        quote_endpoint="/v2/getTransferConfigs",
        status_endpoint="/v2/getTransferStatus",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "bsc", "polygon", "avalanche",
            "fantom", "gnosis", "aurora",
        ],
        known_contract_addresses={
            "ethereum": ["0x5427fefa711eff984124bfbb1ab6fbf5e3da1820"],
            "arbitrum": ["0x1619de6b6b20ed217a58d00f37b9d47c7663feca"],
            "bsc": ["0xdd90e5e87a2081dcf0391920868ebc2ffb81a1af"],
            "polygon": ["0x88dcdc47d2f83a99cf0000fdf667a468bb958a78"],
        },
    ),

    "stargate": BridgeProtocol(
        protocol_id="stargate",
        display_name="Stargate Finance",
        mechanism="liquidity",
        api_base="https://stargate.finance",
        quote_endpoint=None,
        status_endpoint=None,
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "base", "polygon", "avalanche",
            "bsc", "fantom", "linea", "mantle", "metis",
        ],
        known_contract_addresses={
            "ethereum": [
                "0x8731d54E9D02c286767d56ac03e8037C07e01e98",  # Router
                "0xdf0770dF86a8034b3EFEf0A1Bb3c889B8332FF56",  # USDC pool
            ],
            "arbitrum": ["0x53Bf833A5d6c4ddA888F69c22C88C9f356a41614"],
            "optimism": ["0xB0D502E938ed5f4df2E681fE6E419ff29631d62b"],
            "bsc": ["0x4a364f8c717cAAD9A442737Eb7b8A55cc6cf18D8"],
            "avalanche": ["0x45A01E4e04F14f7A4a6702c74187c5F6222033cd"],
            "base": ["0x45f1a95a4d3f3836523f5c83673c797f4d4d263b"],
        },
    ),

    "synapse": BridgeProtocol(
        protocol_id="synapse",
        display_name="Synapse Protocol",
        mechanism="burn_release",
        api_base="https://api.synapseprotocol.com",
        quote_endpoint="/v1/bridge/quote",
        status_endpoint="/v1/bridge/receipts?originTxHash={tx_hash}",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "bsc", "polygon", "avalanche",
            "fantom", "moonriver", "moonbeam", "aurora", "harmony",
        ],
        known_contract_addresses={
            "ethereum": ["0x2796317b0fF8538F253012862c06787Adfb8cEb6"],
            "arbitrum": ["0x6f4e8eba4d337f874ab57478acc2cb5bacdc19c9"],
            "bsc": ["0xd123f70AE324d34A9E76b67a27bf77593bA8749f"],
            "polygon": ["0x8F5BBB2BB8c2Ee94639E55d5F41de9b4839C1280"],
            "avalanche": ["0xC05e61d0E7a63D27546389B7aD62FdFf5A91aACE"],
        },
    ),

    "allbridge": BridgeProtocol(
        protocol_id="allbridge",
        display_name="Allbridge Core",
        mechanism="lock_mint",
        api_base="https://core.api.allbridges.io",
        quote_endpoint="/v1/quote/receive",
        status_endpoint="/v1/receive/{source_chain}/{tx_hash}",
        supported_chains=[
            "ethereum", "bsc", "solana", "polygon", "avalanche", "arbitrum",
            "optimism", "base", "celo", "tron",
        ],
        known_contract_addresses={
            "ethereum": ["0x7DBF07Ad92Ed4e26746Ef4cc6c7BcA8B4849BEBb"],
            "bsc": ["0x7DBF07Ad92Ed4e26746Ef4cc6c7BcA8B4849BEBb"],
            "tron": ["TDPs9gtEqU1iUqiR7g7GKuPi1BMNfCRHV"],
        },
    ),

    "symbiosis": BridgeProtocol(
        protocol_id="symbiosis",
        display_name="Symbiosis Finance",
        mechanism="solver",
        api_base="https://api.symbiosis.finance/crosschain",
        quote_endpoint="/v1/swap",
        status_endpoint="/v1/tx/{tx_hash}",
        supported_chains=[
            "ethereum", "bsc", "polygon", "avalanche", "arbitrum", "optimism",
            "telos", "boba", "mantle", "scroll",
        ],
        known_contract_addresses={
            "ethereum": ["0xb80fDAA74dDA763a8A158ba85798d373A5E84d84"],
            "bsc": ["0x5Aa5f7f84eD0E5db0a4a85C3947eA16B53352FD4"],
            "polygon": ["0xb80fDAA74dDA763a8A158ba85798d373A5E84d84"],
        },
    ),

    "rango": BridgeProtocol(
        protocol_id="rango",
        display_name="Rango Exchange",
        mechanism="solver",
        api_base="https://api.rango.exchange",
        quote_endpoint="/routing/best",
        status_endpoint="/basic/status?requestId={request_id}",
        supported_chains=[
            "ethereum", "bsc", "polygon", "avalanche", "arbitrum", "optimism",
            "solana", "cosmos", "thorchain",
        ],
        known_contract_addresses={
            "ethereum": ["0x69460570c93f9DE5E2edbC3052bf10125f0Ca22d"],
            "bsc": ["0x69460570c93f9DE5E2edbC3052bf10125f0Ca22d"],
            "polygon": ["0x69460570c93f9DE5E2edbC3052bf10125f0Ca22d"],
        },
    ),

    "relay": BridgeProtocol(
        protocol_id="relay",
        display_name="Relay Bridge",
        mechanism="solver",
        api_base="https://api.relay.link",
        quote_endpoint="/quote",
        status_endpoint="/requests/{request_id}",
        supported_chains=[
            "ethereum", "arbitrum", "optimism", "base", "polygon", "zksync",
            "linea", "scroll", "mantle", "mode", "blast",
        ],
        known_contract_addresses={
            "ethereum": ["0xa5f565650890fba1824ee0f21ebbbf660a179934"],
            "arbitrum": ["0xa5f565650890fba1824ee0f21ebbbf660a179934"],
            "base": ["0xa5f565650890fba1824ee0f21ebbbf660a179934"],
            "optimism": ["0xa5f565650890fba1824ee0f21ebbbf660a179934"],
        },
    ),
}


def get_bridge_protocol(protocol_id: str) -> Optional[BridgeProtocol]:
    """Return the BridgeProtocol definition for a given protocol ID, or None."""
    return BRIDGE_REGISTRY.get(protocol_id)


def get_all_contract_addresses() -> Dict[str, List[str]]:
    """Return a flat chain → [address, ...] map covering all registered protocols.

    Used to detect bridge ingress by checking whether a transaction target
    appears in any protocol's known contract address list.
    """
    merged: Dict[str, List[str]] = {}
    for proto in BRIDGE_REGISTRY.values():
        for chain, addrs in proto.known_contract_addresses.items():
            if chain not in merged:
                merged[chain] = []
            merged[chain].extend(a.lower() for a in addrs)
    return merged


def detect_protocol_by_contract(
    chain: str, contract_address: str
) -> Optional[BridgeProtocol]:
    """Return the first bridge protocol whose known contracts include the given address.

    Contract address lookup is case-insensitive.
    """
    target = contract_address.lower()
    for proto in BRIDGE_REGISTRY.values():
        chain_contracts = [
            a.lower() for a in proto.known_contract_addresses.get(chain, [])
        ]
        if target in chain_contracts:
            return proto
    return None
