-- ---------------------------------------------------------------------------
-- T1.16 token_metadata_cache
-- Persistent L2 cache for chain + asset-address token metadata lookups.
-- Used with Redis as L1 for read-through metadata resolution and
-- stale-while-revalidate refreshes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS token_metadata_cache (
    blockchain          TEXT        NOT NULL,
    asset_address       TEXT        NOT NULL,
    symbol              TEXT,
    name                TEXT,
    decimals            INTEGER,
    metadata_uri        TEXT,
    token_standard      TEXT,
    canonical_asset_id  TEXT,
    source              TEXT        NOT NULL DEFAULT 'unknown',
    resolve_status      TEXT        NOT NULL DEFAULT 'resolved',
    lookup_attempts     INTEGER     NOT NULL DEFAULT 0,
    last_error          TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed_at   TIMESTAMPTZ,
    next_refresh_at     TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (blockchain, asset_address)
);

CREATE INDEX IF NOT EXISTS idx_token_metadata_cache_next_refresh
    ON token_metadata_cache (next_refresh_at);

CREATE INDEX IF NOT EXISTS idx_token_metadata_cache_symbol
    ON token_metadata_cache (symbol);

CREATE INDEX IF NOT EXISTS idx_token_metadata_cache_canonical_asset
    ON token_metadata_cache (canonical_asset_id);

ALTER TABLE token_metadata_cache
    ADD CONSTRAINT token_metadata_cache_source_check
    CHECK (source IN ('unknown', 'onchain', 'ipfs', 'solana_mint_account', 'evm_rpc'));

ALTER TABLE token_metadata_cache
    ADD CONSTRAINT token_metadata_cache_resolve_status_check
    CHECK (resolve_status IN ('resolved', 'unresolved', 'error'));
