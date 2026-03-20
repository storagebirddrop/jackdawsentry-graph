-- Migration 010: align bridge_correlations with the Phase 5 runtime contract.
-- Fresh graph environments should match the evolved live schema used by the
-- bridge compiler and fixture tooling.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'bridge_correlations'
          AND column_name = 'protocol'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'bridge_correlations'
          AND column_name = 'protocol_id'
    ) THEN
        ALTER TABLE bridge_correlations RENAME COLUMN protocol TO protocol_id;
    ELSIF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'bridge_correlations'
          AND column_name = 'protocol_id'
    ) THEN
        ALTER TABLE bridge_correlations ADD COLUMN protocol_id VARCHAR(64);
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'bridge_correlations'
          AND column_name = 'protocol'
    ) THEN
        EXECUTE '
            UPDATE bridge_correlations
            SET protocol_id = COALESCE(protocol_id, protocol)
            WHERE protocol_id IS NULL
        ';
    END IF;
END $$;

UPDATE bridge_correlations
SET protocol_id = COALESCE(protocol_id, 'unknown')
WHERE protocol_id IS NULL;

ALTER TABLE bridge_correlations
    ALTER COLUMN protocol_id SET NOT NULL;

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS order_id TEXT;

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS resolution_method TEXT;

ALTER TABLE bridge_correlations
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

DROP INDEX IF EXISTS idx_bridge_corr_protocol;

CREATE INDEX IF NOT EXISTS idx_bridge_corr_protocol
    ON bridge_correlations (protocol_id);

CREATE INDEX IF NOT EXISTS bridge_corr_pending
    ON bridge_correlations (status, next_retry_at)
    WHERE status = 'pending';
