-- Migration 009: Event-store bootstrap backfill state
--
-- Tracks per-chain recent-history backfill progress so fresh installs and
-- redeploys automatically resume event-store bootstrapping without operator
-- intervention.

CREATE TABLE IF NOT EXISTS event_store_backfill_state (
    blockchain              TEXT PRIMARY KEY,
    status                  TEXT        NOT NULL DEFAULT 'pending',
    latest_observed_block   BIGINT,
    target_block            BIGINT      NOT NULL DEFAULT 0,
    next_block              BIGINT      NOT NULL DEFAULT 0,
    attempted_blocks        BIGINT      NOT NULL DEFAULT 0,
    attempted_transactions  BIGINT      NOT NULL DEFAULT 0,
    last_error              TEXT,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_event_store_backfill_status
    ON event_store_backfill_state (status, updated_at DESC);
