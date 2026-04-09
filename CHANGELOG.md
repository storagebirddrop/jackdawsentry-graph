# Changelog

All notable changes to Jackdaw Sentry Graph will be documented in this file.

## [2026-04-09] - Active Graph Contract Docs Alignment

### 📘 Documentation
- Documented direct expand as the active shipped session graph path
- Clarified asset-aware expand behavior for non-Bitcoin address nodes:
  inspector asset selection, stored per-node `Prev` / `Next` reuse, and
  Bitcoin exclusion from the selector path
- Clarified that edge selective trace is `tx_hash`-first and only asset-scoped
  when safe chain-local identity exists for EVM, Solana, and Tron
- Declared `value_fiat` as the canonical active-path edge fiat field
- Declared bridge animation alignment with backend `bridge_source` /
  `bridge_dest`
- Explicitly noted that preview/apply, date-filter, and candidate-selection
  flows are not part of the current shipped path

## [2024-03-22] - Major Code Quality & Performance Improvements

### 🔒 Security & Authentication
- **Fixed authentication bypass vulnerability** - Added proper safeguards to auth bypass mechanism
  - Restricted bypass to non-production environments only
  - Added comprehensive audit logging for bypass events
  - Changed `GRAPH_AUTH_DISABLED` default from `true` to `false`
- **Enhanced boolean flag normalization** - Added `GRAPH_AUTH_DISABLED` to proper validation pipeline

### 🚀 Performance & Reliability
- **Optimized database queries** - Replaced inefficient `COUNT(*)` with `EXISTS` for better performance
- **Improved HTTP client usage** - Eliminated redundant client creation in price oracle and bridge tracer
- **Enhanced async lock initialization** - Implemented lazy initialization pattern to prevent event loop issues
- **Optimized enrichment process** - Added timing metrics and limited enrichment to address nodes only
- **Fixed stale row reclamation** - Added automatic cleanup of stuck address ingest queue entries (5-minute timeout)

### 🐛 Bug Fixes
- **Ethereum collector improvements**:
  - Fixed address padding to preserve leading zeros in 32-byte topics
  - Added proper transaction status determination from receipts
  - Fixed gas and timestamp parsing with robust error handling
  - Replaced hardcoded chain IDs with dynamic mapping for multi-chain support
  - Added missing block-scan fallback to transaction lookup cascade

- **Data processing fixes**:
  - Added validation for malformed DEX logs to prevent KeyError crashes
  - Fixed native token detection using dynamic `_native_symbol()` instead of hardcoded lists
  - Corrected 128-bit integer handling in EVM log decoder
  - Fixed timing calculation precision in bridge tracer

- **Error handling improvements**:
  - Upgraded debug-level logging to warning for better visibility of failures
  - Added proper exception re-raising in address ingest worker
  - Enhanced clipboard error handling with fallback for unsupported browsers

- **Frontend UX improvements**:
  - Fixed subtitle duplication when no entity name exists
  - Improved address shortening logic with better edge case handling
  - Added comprehensive clipboard error handling with fallback

### 🧪 Testing & Quality
- **Fixed test data issues**:
  - Corrected Sui address format to proper 64-character hex
  - Updated TRON address format comments for accuracy
  - Renamed test functions for better clarity
- **Pinned workflow versions** - Fixed semgrep workflow to use specific version instead of floating `@main`

### 🏗️ Architecture Improvements
- **Eliminated duplicate collector registrations** - Implemented alias system with deduplication
- **Enhanced import error handling** - Graceful degradation when optional dependencies unavailable
- **Improved client session management** - Better resource reuse and cleanup patterns

### 📊 Monitoring & Observability
- **Added comprehensive timing metrics** for enrichment operations
- **Enhanced logging** throughout the system for better debugging
- **Improved error visibility** with appropriate log levels and context

---

## Technical Details

### Security Fixes
- Authentication bypass now requires `DEBUG=true` and `ALLOW_AUTH_BYPASS=true`
- All bypass attempts are logged with full context for security auditing
- Default authentication is now enabled by default

### Performance Gains
- Database queries now use `EXISTS` instead of `COUNT(*)` for existence checks
- HTTP clients are reused instead of created per request
- Enrichment only processes address nodes, reducing unnecessary API calls

### Reliability Improvements
- Stale queue entries are automatically reclaimed after 5 minutes
- Malformed log entries are skipped with warnings instead of crashing
- Better error propagation ensures issues are visible to operators

### Code Quality
- Removed hardcoded values throughout the codebase
- Added comprehensive error handling and logging
- Improved type safety and validation
- Enhanced test coverage and data accuracy

This represents a significant improvement in code quality, security, and reliability while maintaining full backward compatibility.
