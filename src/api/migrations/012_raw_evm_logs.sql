-- Migration 012: Raw EVM event logs for DEX swap decoding
--
-- Stores decoded Swap events from DEX contracts (Uniswap V2/V3/V4, Curve,
-- Balancer) so the trace compiler can use ground-truth log data instead of
-- inferring swaps from token-transfer legs alone.
--
-- Only DEX-relevant Swap events are stored — not the full log stream.
-- Populated by the Ethereum/EVM collector when it processes a transaction
-- that involves a known DEX contract.

CREATE TABLE IF NOT EXISTS raw_evm_logs (
    blockchain      TEXT        NOT NULL,
    tx_hash         TEXT        NOT NULL,
    log_index       INTEGER     NOT NULL,
    contract        TEXT        NOT NULL,   -- emitting contract address
    event_sig       TEXT        NOT NULL,   -- topics[0] (keccak256 of event signature)
    topic1          TEXT,
    topic2          TEXT,
    topic3          TEXT,
    data            TEXT,                   -- hex-encoded non-indexed log data
    decoded         JSONB,                  -- decoded field values (amounts, etc.)
    timestamp       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (blockchain, tx_hash, log_index)
) PARTITION BY LIST (blockchain);

CREATE TABLE IF NOT EXISTS raw_evm_logs_ethereum  PARTITION OF raw_evm_logs FOR VALUES IN ('ethereum');
CREATE TABLE IF NOT EXISTS raw_evm_logs_bsc       PARTITION OF raw_evm_logs FOR VALUES IN ('bsc');
CREATE TABLE IF NOT EXISTS raw_evm_logs_polygon   PARTITION OF raw_evm_logs FOR VALUES IN ('polygon');
CREATE TABLE IF NOT EXISTS raw_evm_logs_arbitrum  PARTITION OF raw_evm_logs FOR VALUES IN ('arbitrum');
CREATE TABLE IF NOT EXISTS raw_evm_logs_base      PARTITION OF raw_evm_logs FOR VALUES IN ('base');
CREATE TABLE IF NOT EXISTS raw_evm_logs_avalanche PARTITION OF raw_evm_logs FOR VALUES IN ('avalanche');
CREATE TABLE IF NOT EXISTS raw_evm_logs_optimism  PARTITION OF raw_evm_logs FOR VALUES IN ('optimism');

-- Lookup by contract address + event signature for service-level analysis
CREATE INDEX IF NOT EXISTS idx_evm_logs_contract_event
    ON raw_evm_logs (blockchain, contract, event_sig, timestamp DESC);
