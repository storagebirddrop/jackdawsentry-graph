-- Migration 005: Bridge correlation table
-- Stores pre-computed cross-chain bridge ingress ↔ egress linkage.
-- Populated by a background job; queried at expansion time to insert BridgeNodes.

CREATE TABLE IF NOT EXISTS bridge_correlations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    protocol VARCHAR(64) NOT NULL,
    mechanism VARCHAR(32) NOT NULL,     -- native_amm | lock_mint | burn_release | solver
    source_chain VARCHAR(32) NOT NULL,
    source_tx_hash VARCHAR(128) NOT NULL,
    source_address VARCHAR(128) NOT NULL,
    source_asset VARCHAR(64) NOT NULL,
    source_amount NUMERIC(36, 18),
    source_fiat_value NUMERIC(18, 2),
    destination_chain VARCHAR(32),
    destination_tx_hash VARCHAR(128),
    destination_address VARCHAR(128),
    destination_asset VARCHAR(64),
    destination_amount NUMERIC(36, 18),
    destination_fiat_value NUMERIC(18, 2),
    time_delta_seconds INTEGER,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | completed | failed
    correlation_confidence FLOAT NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'
);

-- Unique index on source side — one ingress tx maps to at most one bridge hop record.
CREATE UNIQUE INDEX IF NOT EXISTS idx_bridge_corr_source
    ON bridge_correlations (source_chain, source_tx_hash);

CREATE INDEX IF NOT EXISTS idx_bridge_corr_dest
    ON bridge_correlations (destination_chain, destination_tx_hash)
    WHERE destination_tx_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bridge_corr_protocol
    ON bridge_correlations (protocol);

CREATE INDEX IF NOT EXISTS idx_bridge_corr_source_addr
    ON bridge_correlations (source_address, source_chain);

CREATE INDEX IF NOT EXISTS idx_bridge_corr_dest_addr
    ON bridge_correlations (destination_address, destination_chain)
    WHERE destination_address IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_bridge_corr_status
    ON bridge_correlations (status)
    WHERE status = 'pending';
