# Jackdaw Sentry Graph - Configuration Reference

## Required Configuration

### Core Secrets

All secrets must be set in `.env` file:

```bash
# Database Credentials
NEO4J_PASSWORD=replace-with-a-long-random-string
POSTGRES_PASSWORD=replace-with-a-long-random-string  
REDIS_PASSWORD=replace-with-a-long-random-string

# Application Security
API_SECRET_KEY=replace-with-a-long-random-string
JWT_SECRET_KEY=replace-with-a-long-random-string
ENCRYPTION_KEY=replace-with-a-long-random-string
```

### Database Configuration

```bash
# PostgreSQL (compliance data)
POSTGRES_DB=jackdawsentry_graph
POSTGRES_USER=jackdawsentry_user
# Host/port configured in docker-compose.yml

# Neo4j (graph data)
NEO4J_DATABASE=neo4j
NEO4J_USER=neo4j
# URI configured in docker-compose.yml

# Redis (cache/queue)
REDIS_DB=0
# Host/port configured in docker-compose.yml
```

## Optional Configuration

### Application Behavior

```bash
# Logging level (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO

# JWT token expiration (minutes)
JWT_EXPIRE_MINUTES=30

# Security settings
TRUST_PROXY_HEADERS=false
EXPOSE_API_DOCS=false                    # Default: false (security)
ENABLE_LEGACY_GRAPH_ENDPOINTS=false       # Default: false
EXPOSE_METRICS=false                       # Default: false
RATE_LIMIT_ENABLED=true                     # Default: true

# Runtime mode flags
DUAL_WRITE_RAW_EVENT_STORE=false          # Default: false
AUTO_BACKFILL_RAW_EVENT_STORE=false         # Default: false
```

### Development Settings

```bash
# Authentication bypass (development only)
GRAPH_AUTH_DISABLED=true
AUTH_DISABLE_CONFIRM=true

# Testing mode
TESTING=false
DEBUG=false
```

## RPC Configuration

### Default Public Endpoints

```bash
# Bitcoin
BITCOIN_RPC_URL=https://bitcoin-rpc.publicnode.com

# Ethereum/EVM Chains
ETHEREUM_RPC_URL=https://ethereum-rpc.publicnode.com
POLYGON_RPC_URL=https://polygon-rpc.com
SOLANA_RPC_URL=https://solana-rpc.publicnode.com
```

### Override Options

You can override any RPC endpoint:

```bash
# Additional EVM chains (configured in ingest overlay)
ARBITRUM_RPC_URL=https://arbitrum-one-rpc.publicnode.com
BASE_RPC_URL=https://base-rpc.publicnode.com
AVALANCHE_RPC_URL=https://avalanche-c-chain-rpc.publicnode.com
OPTIMISM_RPC_URL=https://optimism-rpc.publicnode.com
BSC_RPC_URL=https://bsc-dataseed.binance.org

# Other chains
TRON_RPC_URL=https://api.trongrid.io
XRPL_RPC_URL=https://xrplcluster.com
COSMOS_REST_URL=https://cosmos-rest.publicnode.com
INJECTIVE_REST_URL=https://injective-rest.publicnode.com
STARKNET_RPC_URL=https://starknet-rpc.publicnode.com
SUI_RPC_URL=https://sui-rpc.publicnode.com
```

## Ingest Sidecar Configuration

When using `docker-compose.graph.ingest.yml`, these sidecar-specific overrides apply:

```bash
# Ingest-specific flags (default to true for sidecar only)
GRAPH_INGEST_DUAL_WRITE_RAW_EVENT_STORE=true
GRAPH_INGEST_AUTO_BACKFILL_RAW_EVENT_STORE=true
GRAPH_INGEST_BACKFILL_INTERVAL_SECONDS=30
GRAPH_INGEST_BACKFILL_BLOCK_BATCH_SIZE=2
GRAPH_INGEST_BACKFILL_CHAINS_PER_CYCLE=4
GRAPH_INGEST_BACKFILL_BLOCK_TIMEOUT_SECONDS=120
```

### RPC Fallback Configuration

Ingest overlay supports fallback RPC endpoints:

```bash
# Ethereum
ETHEREUM_RPC_FALLBACK=https://ethereum-rpc.publicnode.com

# Additional EVM chains
ARBITRUM_RPC_FALLBACK=https://arbitrum-one-rpc.publicnode.com
BASE_RPC_FALLBACK=https://base-rpc.publicnode.com
AVALANCHE_RPC_FALLBACK=https://avalanche-c-chain-rpc.publicnode.com
OPTIMISM_RPC_FALLBACK=https://optimism-rpc.publicnode.com
POLYGON_RPC_FALLBACK=https://polygon-bor-rpc.publicnode.com
SOLANA_RPC_FALLBACK=https://solana-rpc.publicnode.com
STARKNET_RPC_FALLBACK=https://starknet-rpc.publicnode.com
```

## API Keys

### Optional Third-party APIs

```bash
# Etherscan API for transaction enrichment
ETHERSCAN_API_KEY=
```

## Network Configuration

### CORS Origins

```bash
# Default allowed origins for development
ALLOWED_ORIGINS=["http://localhost:3000", "http://127.0.0.1:3000"]
```

### Trusted Hosts

Application accepts requests from:
- `localhost`
- `127.0.0.1` 
- `*.jackdawsentry.local`
- `testclient` (testing mode only)

## Configuration Validation

### Required vs Used Variables

| Variable | Required | Used In Code | Notes |
|----------|-----------|----------------|-------|
| NEO4J_PASSWORD | ✅ | ✅ | Neo4j authentication |
| POSTGRES_PASSWORD | ✅ | ✅ | PostgreSQL authentication |
| REDIS_PASSWORD | ✅ | ✅ | Redis authentication |
| API_SECRET_KEY | ✅ | ✅ | JWT signing |
| JWT_SECRET_KEY | ✅ | ✅ | JWT signing |
| ENCRYPTION_KEY | ✅ | ✅ | Data encryption |
| GRAPH_AUTH_DISABLED | ❌ | ✅ | Development bypass |
| ETHERSCAN_API_KEY | ❌ | ✅ | Optional enrichment |

### Unused Documented Variables

The following variables appear in documentation but have limited or no usage:
- Various legacy endpoint flags
- Some experimental feature toggles
- Certain RPC override options not used by base graph stack

## Security Configuration

### Authentication Modes

**Production Mode:**
- `GRAPH_AUTH_DISABLED` not set or `false`
- `AUTH_DISABLE_CONFIRM` not set or `false` 
- Full JWT authentication required

**Development Mode:**
- `NODE_ENV=development`
- `GRAPH_AUTH_DISABLED=true`
- `AUTH_DISABLE_CONFIRM=true`
- Synthetic user created automatically

### API Documentation Access

```bash
# Disable API docs (recommended for production)
EXPOSE_API_DOCS=false

# Enable for trusted environments
EXPOSE_API_DOCS=true
```

When enabled, available at:
- `/docs` (Swagger UI)
- `/redoc` (ReDoc)
- `/openapi.json` (OpenAPI schema)
