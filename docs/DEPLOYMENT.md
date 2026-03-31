# Jackdaw Sentry Graph - Deployment Guide

## Architecture Overview

Jackdaw Sentry Graph is a standalone, self-hosted blockchain investigation tool consisting of:

- **graph-api**: FastAPI backend serving graph session and expansion APIs
- **graph-nginx**: Nginx reverse proxy with packaged React frontend
- **neo4j**: Graph database for address/transaction relationships
- **postgres**: Compliance database and raw event store
- **redis**: Caching and message queuing
- **graph-ingest** (optional): Live blockchain data collection sidecar

## Quick Start

### Base Graph Stack (Request-serving Only)

```bash
# Copy environment template
cp .env.example .env
# Edit .env with your secrets

# Start core services
docker compose -f docker-compose.graph.yml up -d --build
```

Access: http://localhost:8081/

### Optional Ingest Overlay (Live Data Collection)

For live blockchain data collection and backfill:

```bash
# Start with ingest sidecar
docker compose \
  -f docker-compose.graph.yml \
  -f docker-compose.graph.ingest.yml \
  up -d --build
```

This adds the `graph-ingest` service while keeping the request-serving `graph-api` lightweight.

## Services and Ports

| Service | Port | Purpose | Persistence |
|---------|------|---------|------------|
| graph-nginx | 8081 | Frontend serving |
| neo4j | 7475 (HTTP), 7688 (Bolt) | graph_neo4j_data volume |
| postgres | 5433 | graph_postgres_data volume |
| redis | 6380 | graph_redis_data volume |
| graph-api | 8000 (internal) | None |

All services are bound to `127.0.0.1` (localhost only) for security.

## Data Persistence

Named volumes are created automatically:
- `graph_neo4j_data`: Neo4j graph data
- `graph_postgres_data`: PostgreSQL compliance data  
- `graph_redis_data`: Redis cache and queue data

No data is stored in container images - all persistent state is in volumes.

## Configuration Requirements

### Required Secrets

```bash
NEO4J_PASSWORD=replace-with-a-long-random-string
POSTGRES_PASSWORD=replace-with-a-long-random-string  
REDIS_PASSWORD=replace-with-a-long-random-string
API_SECRET_KEY=replace-with-a-long-random-string
JWT_SECRET_KEY=replace-with-a-long-random-string
ENCRYPTION_KEY=replace-with-a-long-random-string
```

### Optional Development Settings

```bash
# Disable authentication (development only)
GRAPH_AUTH_DISABLED=true
AUTH_DISABLE_CONFIRM=true

# Enable API docs (trusted environments only)
EXPOSE_API_DOCS=true
```

### Ingest Sidecar Configuration

When using the ingest overlay, these sidecar-specific defaults apply:

```bash
GRAPH_INGEST_DUAL_WRITE_RAW_EVENT_STORE=true
GRAPH_INGEST_AUTO_BACKFILL_RAW_EVENT_STORE=true
GRAPH_INGEST_BACKFILL_INTERVAL_SECONDS=30
GRAPH_INGEST_BACKFILL_BLOCK_BATCH_SIZE=2
GRAPH_INGEST_BACKFILL_CHAINS_PER_CYCLE=4
```

## Runtime Modes

### Request-serving Mode (Default)
- Graph API serves existing data from Neo4j/PostgreSQL
- No live collectors running
- `Prev`/`Next` expand only existing indexed activity
- Minimal resource usage

### Ingest Mode (Optional Overlay)
- Live blockchain collectors run in dedicated sidecar
- Raw data written to PostgreSQL event store
- Graph API can trigger on-demand ingest for empty frontiers
- Higher resource usage

## Health Checks

- `GET /health`: Basic service health
- `GET /health/detailed`: Database connectivity status
- `GET /api/v1/status`: Authenticated user and ingest status

## Troubleshooting

### Common Issues

**Port conflicts**: Ensure ports 8081, 7475, 7688, 5433, 6380 are available

**Authentication failures**: Check `GRAPH_AUTH_DISABLED` and `AUTH_DISABLE_CONFIRM` in development

**Missing data**: Use ingest overlay or load fixture dataset:
```bash
python scripts/dev/load_perf_fixture_dataset.py
```

**Ingest not detected**: Verify `graph-ingest` container is running and Redis connectivity

### Performance Tuning

- Increase Neo4j memory for large graphs
- Adjust `BACKFILL_BLOCK_BATCH_SIZE` for ingest performance
- Use Redis persistence for cache durability
- Enable Neo4j read replica for read-heavy workloads

## Security Considerations

- All services bind to localhost only by default
- API documentation disabled by default
- JWT tokens expire in 30 minutes
- Rate limiting enabled by default
- No anonymous graph access in production
