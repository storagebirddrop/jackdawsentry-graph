-- Migration 014: add tx_type column to raw_transactions
--
-- Stores the chain-native transaction type string so chain compilers can
-- detect swap activity by type rather than contract address.
--
-- XRP Ledger:  TransactionType (e.g. "AMMSwap", "OfferCreate", "Payment")
-- Cosmos Hub:  First message @type (e.g. "/cosmos.bank.v1beta1.MsgSend",
--              "/osmosis.gamm.v1beta1.MsgSwapExactAmountIn")
-- Other chains: NULL — field unused unless the collector populates it.
--
-- Added with DEFAULT NULL and no NOT NULL constraint so existing rows are
-- unaffected and backfill can proceed lazily.

ALTER TABLE raw_transactions
    ADD COLUMN IF NOT EXISTS tx_type TEXT DEFAULT NULL;

-- Index supports WHERE tx_type = 'AMMSwap' style queries in chain compilers.
CREATE INDEX IF NOT EXISTS idx_raw_transactions_tx_type
    ON raw_transactions (blockchain, tx_type)
    WHERE tx_type IS NOT NULL;
