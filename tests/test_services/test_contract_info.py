"""Unit tests for the contract deployer/creator resolution service.

Covers:
- EVM (Etherscan v2): contract found → ContractInfo with deployer + tx hash.
- EVM: EOA (status "0") → ContractInfo(is_contract=False).
- EVM: API error (HTTP exception) → ContractInfo(is_contract=False), no raise.
- EVM: rate-limit response → None (not cached).
- EVM: unsupported chain → None.
- Solana: non-executable account → ContractInfo(is_contract=False).
- Solana: upgradeable program with authority → ContractInfo with upgrade_authority.
- Solana: immutable program (non-upgradeable loader) → ContractInfo, no authority.
- Solana: RPC error → ContractInfo(is_contract=False), no raise.
- Redis caching: cache hit returns ContractInfo without HTTP call.
- Redis caching: result is written after live lookup.
- Redis caching: rate-limit (None) is NOT written to cache.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.contract_info import (
    ContractInfo,
    _CACHE_PREFIX,
    _ETHERSCAN_CHAIN_IDS,
    get_contract_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _etherscan_ok(deployer: str = "0xdeployer", tx: str = "0xtx") -> dict:
    """Stub Etherscan v2 success response."""
    return {
        "status": "1",
        "message": "OK",
        "result": [{"contractCreator": deployer, "txHash": tx}],
    }


def _etherscan_eoa() -> dict:
    return {"status": "0", "message": "No data found", "result": None}


def _etherscan_rate_limit() -> dict:
    return {"status": "0", "message": "Max rate limit reached", "result": None}


def _mock_http_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    resp.status_code = status_code
    return resp


def _solana_executable_account(
    is_executable: bool = True,
    owner: str = "BPFLoaderUpgradeab1e11111111111111111111111",
    program_data: str = "ProgData1111111111111111111111111111111111",
) -> dict:
    return {
        "result": {
            "value": {
                "executable": is_executable,
                "owner": owner,
                "data": {
                    "parsed": {
                        "info": {
                            "programData": program_data,
                        }
                    }
                },
            }
        }
    }


def _solana_program_data(authority: str = "0xauthority11111111111111111111") -> dict:
    return {
        "result": {
            "value": {
                "data": {
                    "parsed": {
                        "info": {
                            "authority": authority,
                        }
                    }
                }
            }
        }
    }


# ---------------------------------------------------------------------------
# EVM tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evm_contract_found():
    """Etherscan returns a creator → ContractInfo.is_contract=True with fields."""
    mock_resp = _mock_http_response(_etherscan_ok("0xdeployer", "0xtxhash"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("0xcontract", "ethereum")

    assert result is not None
    assert result.is_contract is True
    assert result.deployer == "0xdeployer"
    assert result.deployment_tx == "0xtxhash"
    assert result.chain == "ethereum"


@pytest.mark.asyncio
async def test_evm_eoa_returns_not_contract():
    """Etherscan returns status='0' → EOA, is_contract=False."""
    mock_resp = _mock_http_response(_etherscan_eoa())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("0xeoa", "bsc")

    assert result is not None
    assert result.is_contract is False


@pytest.mark.asyncio
async def test_evm_http_error_degrades_gracefully():
    """HTTP exception → returns ContractInfo(is_contract=False), does not raise."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(side_effect=Exception("timeout"))

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("0xcontract", "polygon")

    assert result is not None
    assert result.is_contract is False


@pytest.mark.asyncio
async def test_evm_rate_limit_returns_none():
    """Rate-limit response → returns None so caller does not cache."""
    mock_resp = _mock_http_response(_etherscan_rate_limit())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("0xcontract", "ethereum")

    assert result is None


@pytest.mark.asyncio
async def test_unsupported_chain_returns_none():
    """Chain not in ETHERSCAN_CHAIN_IDS and not solana → None immediately."""
    result = await get_contract_info("0xcontract", "bitcoin")
    assert result is None


@pytest.mark.asyncio
async def test_evm_bsc_uses_correct_chain_id():
    """BSC lookup uses chainid=56 in the request params."""
    mock_resp = _mock_http_response(_etherscan_ok())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        await get_contract_info("0xcontract", "bsc")

    call_kwargs = mock_client.get.call_args
    params = call_kwargs[1].get("params") or call_kwargs[0][1]
    assert params["chainid"] == _ETHERSCAN_CHAIN_IDS["bsc"] == 56


# ---------------------------------------------------------------------------
# Solana tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solana_non_executable_returns_not_contract():
    """Non-executable Solana account → is_contract=False."""
    rpc_resp = _mock_http_response(_solana_executable_account(is_executable=False))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=rpc_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("SomeWalletAddr11111111111111111111", "solana")

    assert result is not None
    assert result.is_contract is False


@pytest.mark.asyncio
async def test_solana_upgradeable_program_has_authority():
    """Upgradeable BPF program → is_contract=True with upgrade_authority set."""
    account_resp = _mock_http_response(
        _solana_executable_account(
            is_executable=True,
            owner="BPFLoaderUpgradeab1e11111111111111111111111",
            program_data="ProgData11111",
        )
    )
    pd_resp = _mock_http_response(_solana_program_data("AuthAddr11111"))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=[account_resp, pd_resp])

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("ProgramAddr1111111111111111111111", "solana")

    assert result is not None
    assert result.is_contract is True
    assert result.upgrade_authority == "AuthAddr11111"
    assert result.deployer == "AuthAddr11111"  # linter assigned deployer = upgrade_authority


@pytest.mark.asyncio
async def test_solana_immutable_program_no_authority():
    """Program with non-upgradeable loader → is_contract=True, no upgrade_authority."""
    account_resp = _mock_http_response(
        _solana_executable_account(
            is_executable=True,
            owner="BPFLoader2111111111111111111111111111111111",  # v2, not upgradeable
        )
    )
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=account_resp)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("ImmutableProg11111111111111111111", "solana")

    assert result is not None
    assert result.is_contract is True
    assert result.upgrade_authority is None


@pytest.mark.asyncio
async def test_solana_rpc_error_degrades_gracefully():
    """Solana RPC exception → ContractInfo(is_contract=False), does not raise."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=Exception("RPC down"))

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("SomeProgAddr1111111111111111111111", "solana")

    assert result is not None
    assert result.is_contract is False


# ---------------------------------------------------------------------------
# Redis caching tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_http():
    """When Redis has a cached entry, no HTTP call is made."""
    cached = json.dumps({
        "is_contract": True,
        "deployer": "0xcached",
        "deployment_tx": "0xtxcached",
        "upgrade_authority": None,
        "chain": "ethereum",
    })
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached)
    redis.set = AsyncMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_http):
        result = await get_contract_info("0xcontract", "ethereum", redis_client=redis)

    assert result is not None
    assert result.is_contract is True
    assert result.deployer == "0xcached"
    mock_http.get.assert_not_called()


@pytest.mark.asyncio
async def test_cache_written_after_live_lookup():
    """Successful live lookup writes result to Redis with 7-day TTL."""
    mock_resp = _mock_http_response(_etherscan_ok("0xdeployer", "0xtxhash"))
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)  # cache miss
    redis.set = AsyncMock()

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        await get_contract_info("0xcontract", "ethereum", redis_client=redis)

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    key = call_args[0][0]
    assert key.startswith(_CACHE_PREFIX)
    # TTL should be 7 days = 604800 seconds.
    ttl = call_args[1].get("ex") or call_args[0][2]
    assert ttl == 7 * 24 * 3600


@pytest.mark.asyncio
async def test_rate_limit_not_written_to_cache():
    """Rate-limit (None result) must not be written to Redis."""
    mock_resp = _mock_http_response(_etherscan_rate_limit())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()

    with patch("src.services.contract_info.httpx.AsyncClient", return_value=mock_client):
        result = await get_contract_info("0xcontract", "ethereum", redis_client=redis)

    assert result is None
    redis.set.assert_not_awaited()
