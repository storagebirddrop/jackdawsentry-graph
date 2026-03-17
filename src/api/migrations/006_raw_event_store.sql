-- Migration 006: Raw event store — PostgreSQL raw blockchain fact tables.
--
-- Implements ADR-002: all raw immutable blockchain facts move out of Neo4j into
-- partitioned PostgreSQL tables.  The trace compiler reads from here; Neo4j
-- receives only derived investigation-graph nodes.
--
-- T1.1  raw_transactions, raw_token_transfers (LIST-partitioned by blockchain)
-- T1.2  raw_utxo_inputs, raw_utxo_outputs
-- T1.3  raw_solana_instructions, solana_ata_owners, solana_alt_addresses
-- T1.4  raw_bridge_events
-- T1.5  bridge_correlations — ALTER to add missing columns
-- T1.6  asset_prices
-- T1.7  all required indexes

-- ---------------------------------------------------------------------------
-- T1.1  raw_transactions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_transactions (
    id                  BIGSERIAL,
    blockchain          TEXT        NOT NULL,
    tx_hash             TEXT        NOT NULL,
    block_number        BIGINT,
    timestamp           TIMESTAMPTZ NOT NULL,
    from_address        TEXT,
    to_address          TEXT,
    value_raw           NUMERIC(38, 0),
    value_native        FLOAT,
    gas_used            BIGINT,
    gas_price           NUMERIC(38, 0),
    status              TEXT,           -- "success" | "failed" | "pending"
    input_data          BYTEA,
    is_bridge_ingress   BOOLEAN     NOT NULL DEFAULT FALSE,
    is_bridge_egress    BOOLEAN     NOT NULL DEFAULT FALSE,
    bridge_protocol     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (blockchain, id)
) PARTITION BY LIST (blockchain);

-- Per-chain partitions for the 16 supported blockchains.
CREATE TABLE IF NOT EXISTS raw_transactions_ethereum   PARTITION OF raw_transactions FOR VALUES IN ('ethereum');
CREATE TABLE IF NOT EXISTS raw_transactions_bitcoin    PARTITION OF raw_transactions FOR VALUES IN ('bitcoin');
CREATE TABLE IF NOT EXISTS raw_transactions_solana     PARTITION OF raw_transactions FOR VALUES IN ('solana');
CREATE TABLE IF NOT EXISTS raw_transactions_tron       PARTITION OF raw_transactions FOR VALUES IN ('tron');
CREATE TABLE IF NOT EXISTS raw_transactions_xrp        PARTITION OF raw_transactions FOR VALUES IN ('xrp');
CREATE TABLE IF NOT EXISTS raw_transactions_bsc        PARTITION OF raw_transactions FOR VALUES IN ('bsc');
CREATE TABLE IF NOT EXISTS raw_transactions_polygon    PARTITION OF raw_transactions FOR VALUES IN ('polygon');
CREATE TABLE IF NOT EXISTS raw_transactions_arbitrum   PARTITION OF raw_transactions FOR VALUES IN ('arbitrum');
CREATE TABLE IF NOT EXISTS raw_transactions_base       PARTITION OF raw_transactions FOR VALUES IN ('base');
CREATE TABLE IF NOT EXISTS raw_transactions_avalanche  PARTITION OF raw_transactions FOR VALUES IN ('avalanche');
CREATE TABLE IF NOT EXISTS raw_transactions_optimism   PARTITION OF raw_transactions FOR VALUES IN ('optimism');
CREATE TABLE IF NOT EXISTS raw_transactions_starknet   PARTITION OF raw_transactions FOR VALUES IN ('starknet');
CREATE TABLE IF NOT EXISTS raw_transactions_injective  PARTITION OF raw_transactions FOR VALUES IN ('injective');
CREATE TABLE IF NOT EXISTS raw_transactions_cosmos     PARTITION OF raw_transactions FOR VALUES IN ('cosmos');
CREATE TABLE IF NOT EXISTS raw_transactions_sui        PARTITION OF raw_transactions FOR VALUES IN ('sui');
CREATE TABLE IF NOT EXISTS raw_transactions_lightning  PARTITION OF raw_transactions FOR VALUES IN ('lightning');

-- Unique hash index per-chain.
CREATE UNIQUE INDEX IF NOT EXISTS raw_tx_unique
    ON raw_transactions (blockchain, tx_hash);

-- ---------------------------------------------------------------------------
-- T1.1  raw_token_transfers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_token_transfers (
    id                  BIGSERIAL,
    blockchain          TEXT        NOT NULL,
    tx_hash             TEXT        NOT NULL,
    transfer_index      INTEGER     NOT NULL,
    asset_symbol        TEXT,
    asset_contract      TEXT,
    canonical_asset_id  TEXT,
    from_address        TEXT        NOT NULL,
    to_address          TEXT        NOT NULL,
    amount_raw          NUMERIC(38, 0),
    amount_normalized   FLOAT,
    timestamp           TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (blockchain, tx_hash, transfer_index)
) PARTITION BY LIST (blockchain);

CREATE TABLE IF NOT EXISTS raw_token_transfers_ethereum   PARTITION OF raw_token_transfers FOR VALUES IN ('ethereum');
CREATE TABLE IF NOT EXISTS raw_token_transfers_bitcoin    PARTITION OF raw_token_transfers FOR VALUES IN ('bitcoin');
CREATE TABLE IF NOT EXISTS raw_token_transfers_solana     PARTITION OF raw_token_transfers FOR VALUES IN ('solana');
CREATE TABLE IF NOT EXISTS raw_token_transfers_tron       PARTITION OF raw_token_transfers FOR VALUES IN ('tron');
CREATE TABLE IF NOT EXISTS raw_token_transfers_xrp        PARTITION OF raw_token_transfers FOR VALUES IN ('xrp');
CREATE TABLE IF NOT EXISTS raw_token_transfers_bsc        PARTITION OF raw_token_transfers FOR VALUES IN ('bsc');
CREATE TABLE IF NOT EXISTS raw_token_transfers_polygon    PARTITION OF raw_token_transfers FOR VALUES IN ('polygon');
CREATE TABLE IF NOT EXISTS raw_token_transfers_arbitrum   PARTITION OF raw_token_transfers FOR VALUES IN ('arbitrum');
CREATE TABLE IF NOT EXISTS raw_token_transfers_base       PARTITION OF raw_token_transfers FOR VALUES IN ('base');
CREATE TABLE IF NOT EXISTS raw_token_transfers_avalanche  PARTITION OF raw_token_transfers FOR VALUES IN ('avalanche');
CREATE TABLE IF NOT EXISTS raw_token_transfers_optimism   PARTITION OF raw_token_transfers FOR VALUES IN ('optimism');
CREATE TABLE IF NOT EXISTS raw_token_transfers_starknet   PARTITION OF raw_token_transfers FOR VALUES IN ('starknet');
CREATE TABLE IF NOT EXISTS raw_token_transfers_injective  PARTITION OF raw_token_transfers FOR VALUES IN ('injective');
CREATE TABLE IF NOT EXISTS raw_token_transfers_cosmos     PARTITION OF raw_token_transfers FOR VALUES IN ('cosmos');
CREATE TABLE IF NOT EXISTS raw_token_transfers_sui        PARTITION OF raw_token_transfers FOR VALUES IN ('sui');
CREATE TABLE IF NOT EXISTS raw_token_transfers_lightning  PARTITION OF raw_token_transfers FOR VALUES IN ('lightning');

-- ---------------------------------------------------------------------------
-- T1.2  raw_utxo_inputs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_utxo_inputs (
    blockchain          TEXT        NOT NULL,
    tx_hash             TEXT        NOT NULL,
    input_index         INTEGER     NOT NULL,
    prev_tx_hash        TEXT        NOT NULL,
    prev_output_index   INTEGER     NOT NULL,
    address             TEXT,
    value_satoshis      BIGINT,
    script_type         TEXT,
    sequence            BIGINT,
    timestamp           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (blockchain, tx_hash, input_index)
);

-- ---------------------------------------------------------------------------
-- T1.2  raw_utxo_outputs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_utxo_outputs (
    blockchain          TEXT        NOT NULL,
    tx_hash             TEXT        NOT NULL,
    output_index        INTEGER     NOT NULL,
    address             TEXT,
    value_satoshis      BIGINT,
    script_type         TEXT,           -- "p2pkh" | "p2sh" | "p2wpkh" | "p2wsh" | "p2tr" | "op_return"
    is_probable_change  BOOLEAN,
    is_spent            BOOLEAN     NOT NULL DEFAULT FALSE,
    spending_tx_hash    TEXT,
    timestamp           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (blockchain, tx_hash, output_index)
);

-- ---------------------------------------------------------------------------
-- T1.3  raw_solana_instructions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_solana_instructions (
    tx_signature        TEXT        NOT NULL,
    ix_index            INTEGER     NOT NULL,
    program_id          TEXT        NOT NULL,
    program_name        TEXT,
    instruction_type    TEXT,
    decoded_args        JSONB,
    raw_accounts        TEXT[],
    resolved_accounts   JSONB,
    decode_status       TEXT,           -- "success" | "partial" | "raw"
    timestamp           TIMESTAMPTZ NOT NULL,
    slot                BIGINT,
    PRIMARY KEY (tx_signature, ix_index)
);

-- T1.3  solana_ata_owners — ATA resolution cache.
CREATE TABLE IF NOT EXISTS solana_ata_owners (
    ata_address         TEXT        NOT NULL,
    owner_address       TEXT        NOT NULL,
    mint_address        TEXT        NOT NULL,
    resolved_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (ata_address)
);

-- T1.3  solana_alt_addresses — Address Lookup Table resolution cache.
CREATE TABLE IF NOT EXISTS solana_alt_addresses (
    alt_account         TEXT        NOT NULL,
    slot                BIGINT      NOT NULL,
    addresses           TEXT[]      NOT NULL,
    resolved_at         TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (alt_account, slot)
);

-- ---------------------------------------------------------------------------
-- T1.4  raw_bridge_events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_bridge_events (
    blockchain          TEXT        NOT NULL,
    tx_hash             TEXT        NOT NULL,
    event_index         INTEGER     NOT NULL,
    bridge_protocol     TEXT        NOT NULL,
    event_type          TEXT,           -- "deposit"|"lock"|"emit_vaa"|"relay"|"redeem"|"fulfill"
    raw_log             JSONB,
    decoded             JSONB,
    order_id            TEXT,           -- solver-pattern protocols: deBridge, Squid, etc.
    timestamp           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (blockchain, tx_hash, event_index)
);

-- ---------------------------------------------------------------------------
-- T1.5  bridge_correlations — extend existing table with missing columns
-- ---------------------------------------------------------------------------

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS retry_count      INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_retry_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS order_id         TEXT,
    ADD COLUMN IF NOT EXISTS resolution_method TEXT,
    ADD COLUMN IF NOT EXISTS resolved_at      TIMESTAMPTZ;

-- Rename protocol → protocol_id for consistency with the canonical model.
-- Only rename if the old column exists and the new one does not.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'bridge_correlations' AND column_name = 'protocol'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'bridge_correlations' AND column_name = 'protocol_id'
    ) THEN
        ALTER TABLE bridge_correlations RENAME COLUMN protocol TO protocol_id;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- T1.6  asset_prices
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS asset_prices (
    canonical_asset_id  TEXT        NOT NULL,
    timestamp_hour      TIMESTAMPTZ NOT NULL,   -- rounded to the hour
    price_usd           FLOAT       NOT NULL,
    source              TEXT,                   -- "coingecko" | "chainlink"
    PRIMARY KEY (canonical_asset_id, timestamp_hour)
);

-- ---------------------------------------------------------------------------
-- T1.7  indexes on event store tables
-- ---------------------------------------------------------------------------

-- raw_transactions
CREATE INDEX IF NOT EXISTS raw_tx_address_time
    ON raw_transactions (blockchain, from_address, timestamp);
CREATE INDEX IF NOT EXISTS raw_tx_address_time_to
    ON raw_transactions (blockchain, to_address, timestamp);

-- raw_token_transfers
CREATE INDEX IF NOT EXISTS raw_transfer_from
    ON raw_token_transfers (blockchain, from_address, timestamp);
CREATE INDEX IF NOT EXISTS raw_transfer_to
    ON raw_token_transfers (blockchain, to_address, timestamp);
CREATE INDEX IF NOT EXISTS raw_transfer_canonical
    ON raw_token_transfers (canonical_asset_id, timestamp);

-- raw_utxo_outputs
CREATE INDEX IF NOT EXISTS raw_utxo_out_address
    ON raw_utxo_outputs (blockchain, address, timestamp);

-- raw_utxo_inputs
CREATE INDEX IF NOT EXISTS raw_utxo_in_prev
    ON raw_utxo_inputs (blockchain, prev_tx_hash, prev_output_index);

-- raw_solana_instructions
CREATE INDEX IF NOT EXISTS solana_ix_program
    ON raw_solana_instructions (program_id, timestamp);

-- raw_bridge_events
CREATE INDEX IF NOT EXISTS bridge_event_protocol
    ON raw_bridge_events (bridge_protocol, blockchain, timestamp);
CREATE INDEX IF NOT EXISTS bridge_event_order
    ON raw_bridge_events (order_id)
    WHERE order_id IS NOT NULL;

-- bridge_correlations (new columns)
CREATE INDEX IF NOT EXISTS bridge_corr_pending
    ON bridge_correlations (status, next_retry_at)
    WHERE status = 'pending';

-- solana_ata_owners
CREATE INDEX IF NOT EXISTS ata_owner_lookup
    ON solana_ata_owners (owner_address);
