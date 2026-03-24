-- Migration 011: Address-targeted on-demand ingest queue
--
-- When a trace-compiler expansion hits an empty frontier (no events in
-- raw_transactions / raw_token_transfers for a given address) it queues a
-- targeted backfill request here.  The AddressIngestWorker processes these
-- rows in priority order using the chain-specific collector.

CREATE TABLE IF NOT EXISTS address_ingest_queue (
    id              BIGSERIAL PRIMARY KEY,
    address         TEXT        NOT NULL,
    blockchain      TEXT        NOT NULL,
    priority        SMALLINT    NOT NULL DEFAULT 0,   -- higher = sooner
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    tx_count        INT,                               -- rows written on success
    error           TEXT,                              -- last error message on failure
    retry_count     SMALLINT    NOT NULL DEFAULT 0,
    next_retry_at   TIMESTAMPTZ
);

-- One pending/running row per (address, blockchain) at a time; duplicates
-- are handled by the upsert in the trigger module.
CREATE UNIQUE INDEX IF NOT EXISTS uix_address_ingest_queue_active
    ON address_ingest_queue (address, blockchain)
    WHERE status IN ('pending', 'running');

-- Worker polling: pending rows first, then by priority desc, then FIFO.
CREATE INDEX IF NOT EXISTS idx_address_ingest_queue_poll
    ON address_ingest_queue (status, priority DESC, requested_at ASC)
    WHERE status IN ('pending', 'running');

-- Retry queries: efficiently find failed rows that are ready for retry
CREATE INDEX IF NOT EXISTS idx_address_ingest_queue_retry
    ON address_ingest_queue (status, next_retry_at)
    WHERE status = 'failed' AND next_retry_at IS NOT NULL;
