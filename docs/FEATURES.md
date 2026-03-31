# Jackdaw Sentry Graph - Feature Status

## Supported Blockchains (14)

**Fully Supported:**
- Bitcoin (UTXO tracing with CoinJoin detection)
- Ethereum (EVM DEX swap detection)
- BSC (Binance Smart Chain)
- Polygon  
- Arbitrum
- Base
- Avalanche
- Optimism
- Solana (Instruction-level parsing, SPL tokens)
- Sui
- Starknet
- Injective
- Cosmos
- Tron

**Chain Implementation Notes:**
- All EVM chains share the same compiler with chain-specific RPC endpoints
- Bitcoin uses UTXO model with change detection and CoinJoin halts
- Solana provides ATA resolution to owner wallets when possible
- XRP is **NOT SUPPORTED** despite being listed in README

## Core Graph Features

### ✅ Fully Implemented

**Session Management**
- Backend-owned session persistence and restore
- Recent session discovery via `/api/v1/graph/sessions/recent`
- Autosave with conflict protection
- Session snapshot revision tracking

**Graph Expansion**
- ExpansionResponse v2 with full lineage metadata
- Directional expansion (next/previous/neighbors)
- Empty-state honesty explanations
- Bridge hop expansion with status polling
- On-demand address ingest trigger

**Multi-chain Tracing**
- Cross-chain bridge detection and correlation
- EVM DEX swap event promotion
- Solana instruction parsing (Jupiter, Raydium, Wormhole)
- UTXO-level Bitcoin tracing
- Calldata-based destination decoding

**Security & Compliance**
- JWT authentication with 30-minute expiration
- Sanctions screening integration framework
- Entity attribution system
- Rate limiting enabled by default
- API documentation disabled by default

### ⚠️ Partially Implemented

**Entity Attribution**
- Framework exists for address clustering and labeling
- Limited entity data sources in public repo
- Requires private datasets for full coverage

**Lightning Network**
- Channel open and close event markers only
- No in-channel routing visualization
- Peg-in/peg-out events marked but not traced internally

**Liquid Network**
- Peg-in and peg-out event markers
- No internal Liquid network tracing
- Treated as cross-chain bridge endpoint

### ❌ Not Implemented

**XRP Support**
- Collector exists but not integrated into supported chains list
- No graph compiler implementation
- Cannot be used for investigation

## API Endpoints

### Current (ExpansionResponse v2)
- `POST /api/v1/graph/sessions` - Create investigation session
- `GET /api/v1/graph/sessions/{session_id}` - Load session
- `POST /api/v1/graph/sessions/{session_id}/expand` - Expand graph with lineage
- `POST /api/v1/graph/sessions/{session_id}/snapshot` - Save session state
- `GET /api/v1/graph/sessions/recent` - Discover recent sessions

### Deprecated (Legacy Flat Graph)
- `POST /api/v1/graph/expand` - Sunset 2026-06-30
- `POST /api/v1/graph/trace` - Sunset 2026-06-30  
- `POST /api/v1/graph/search` - Sunset 2026-06-30
- `POST /api/v1/graph/cluster` - Sunset 2026-06-30

## Frontend Capabilities

**Graph Visualization**
- React 19 + TypeScript with XYFlow layout engine
- Real-time bridge polling and status updates
- Branch-focused investigation workflows
- Session briefings and compare views

**Investigation Tools**
- Route focus for protocol/analysis slicing
- Bridge intelligence cards with protocol classification
- Pinned path stories for narrative tracking
- Node inspector with detailed lineage information

## Limitations

**Technical Limits**
- Maximum 500 nodes per expansion request
- Maximum depth of 5 hops for trace operations
- Maximum 50 addresses per cluster request

**Chain-specific Limits**
- Lightning: No in-channel transaction tracing
- Liquid: No internal network tracing
- XRP: Not supported despite collector existence

**Data Dependencies**
- Requires pre-populated event store for full historical analysis
- Live RPC fallback depends on public node availability
- Entity attribution requires external datasets for full coverage
