-- Migration 015: Add transfer_index to raw_transactions.
--
-- Solana transactions can contain multiple native SOL system-program transfers
-- within a single tx_hash.  The original unique index on (blockchain, tx_hash)
-- dropped all but the first transfer; this migration adds a transfer_index
-- column (defaulting to 0 for all existing rows and EVM chains) and updates
-- the unique constraint to (blockchain, tx_hash, transfer_index).

-- Step 1: add the column with default 0 so existing rows are unaffected.
ALTER TABLE raw_transactions
    ADD COLUMN IF NOT EXISTS transfer_index INTEGER NOT NULL DEFAULT 0;

-- Step 2: create the new composite unique index under a temporary name so that
-- uniqueness enforcement is never absent (no window between drop and create).
CREATE UNIQUE INDEX IF NOT EXISTS raw_tx_unique_new
    ON raw_transactions (blockchain, tx_hash, transfer_index);

-- Step 3: drop the old two-column unique index now that the new one is in place.
DROP INDEX IF EXISTS raw_tx_unique;

-- Step 4: rename the new index to the canonical name (idempotent — skips when
-- raw_tx_unique_new is absent or raw_tx_unique already exists).
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'raw_tx_unique_new' AND n.nspname = current_schema()
  ) AND NOT EXISTS (
    SELECT 1 FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relname = 'raw_tx_unique' AND n.nspname = current_schema()
  ) THEN
    ALTER INDEX raw_tx_unique_new RENAME TO raw_tx_unique;
  END IF;
END
$$;
