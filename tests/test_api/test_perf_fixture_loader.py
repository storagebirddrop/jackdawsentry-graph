from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_fixture_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "dev"
        / "load_perf_fixture_dataset.py"
    )
    spec = importlib.util.spec_from_file_location("load_perf_fixture_dataset", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_high_degree_fixture_bundle_is_deterministic_and_dense():
    module = _load_fixture_module()

    bundle = module.build_high_degree_evm_fixture()

    assert bundle.chain == "ethereum"
    assert bundle.seed_address == "0xfeed00000000000000000000000000000000cafe"
    assert len(bundle.transactions) == 200
    assert len(bundle.token_transfers) == 60
    assert len(bundle.asset_prices) == 2
    assert len(bundle.neo4j_transactions) == len(bundle.transactions)

    tx_hashes = {row["tx_hash"] for row in bundle.transactions}
    token_hashes = {row["tx_hash"] for row in bundle.token_transfers}
    assert len(tx_hashes) == len(bundle.transactions)
    assert len(token_hashes) == len(bundle.token_transfers)

    outbound = [row for row in bundle.transactions if row["from_address"] == bundle.seed_address]
    inbound = [row for row in bundle.transactions if row["to_address"] == bundle.seed_address]
    assert len(outbound) == 80
    assert len(inbound) == 80

    token_outbound = [
        row for row in bundle.token_transfers if row["from_address"] == bundle.seed_address
    ]
    token_inbound = [
        row for row in bundle.token_transfers if row["to_address"] == bundle.seed_address
    ]
    assert len(token_outbound) == 30
    assert len(token_inbound) == 30
