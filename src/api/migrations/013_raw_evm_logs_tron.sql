-- Migration 013: Add Tron partition to raw_evm_logs
--
-- JustSwap and SunSwap V1/V2/V3 are Uniswap V2/V3 forks running on the Tron
-- Virtual Machine (TVM).  Their Swap events use the identical keccak256
-- signatures as their EVM counterparts, so the same raw_evm_logs schema
-- applies.
--
-- The TronCollector fetches log data via the TronGrid
-- wallet/gettransactioninfobyid endpoint and dual-writes matching Swap events
-- to this partition.  TronChainCompiler._try_swap_promotion then reads from
-- this partition to build ground-truth swap_event nodes.
--
-- Safe to apply multiple times (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS raw_evm_logs_tron
    PARTITION OF raw_evm_logs
    FOR VALUES IN ('tron');
