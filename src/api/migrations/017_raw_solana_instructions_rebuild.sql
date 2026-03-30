-- ---------------------------------------------------------------------------
-- Migration 017: rebuild raw_solana_instructions with the schema base.py uses
--
-- The table created in 006 used the live-fetch schema (tx_signature, decoded_args).
-- base.py._insert_raw_solana_instructions writes a transfer-oriented schema
-- (blockchain, tx_hash, instruction_index, from/to_address, asset fields).
-- These are incompatible: different column names, different primary key.
--
-- Strategy: archive the 006 table (retain any rows written by live_fetch),
-- then create the correct table for the collector dual-write path.
-- ---------------------------------------------------------------------------

-- Preserve any existing rows from the live-fetch path.
ALTER TABLE IF EXISTS raw_solana_instructions
    RENAME TO raw_solana_instructions_006_archive;

CREATE TABLE IF NOT EXISTS raw_solana_instructions (
    blockchain          TEXT            NOT NULL,
    tx_hash             TEXT            NOT NULL,
    instruction_index   INTEGER         NOT NULL,
    program_id          TEXT,
    from_address        TEXT,
    to_address          TEXT,
    asset_symbol        TEXT,
    asset_contract      TEXT,
    canonical_asset_id  TEXT,
    amount_raw          TEXT,
    amount_normalized   DOUBLE PRECISION,
    timestamp           TIMESTAMPTZ     NOT NULL,
    PRIMARY KEY (blockchain, tx_hash, instruction_index)
);

CREATE INDEX IF NOT EXISTS raw_solana_ix_program
    ON raw_solana_instructions (program_id, timestamp);

CREATE INDEX IF NOT EXISTS raw_solana_ix_from_address
    ON raw_solana_instructions (from_address, blockchain);
