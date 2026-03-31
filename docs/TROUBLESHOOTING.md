# Jackdaw Sentry Graph - Troubleshooting Guide

## Common Issues

### Port Conflicts

**Problem**: Services fail to start due to port conflicts
**Solution**: Ensure these ports are available:
- 8081 (nginx/frontend)
- 7475 (Neo4j HTTP)
- 7688 (Neo4j Bolt)
- 5433 (PostgreSQL)
- 6380 (Redis)

Check for existing processes:
```bash
# Check if ports are in use
netstat -tulpn | grep -E ':(8081|7475|7688|5433|6380)'

# Kill existing Docker containers
docker compose -f docker-compose.graph.yml down
```

### Authentication Issues

**Problem**: Login fails with "Invalid username or password"
**Solutions**:
1. Verify user exists:
```bash
docker exec jackdawsentry_graph_api python -m scripts.dev.create_user.py your-username your-password
```

2. Check auth bypass settings:
```bash
# For development only
echo "GRAPH_AUTH_DISABLED=true" >> .env
echo "AUTH_DISABLE_CONFIRM=true" >> .env
```

3. Restart services:
```bash
docker compose -f docker-compose.graph.yml up --force-recreate
```

### Empty Graph State

**Problem**: Graph shows no nodes or "No indexed activity exists"
**Causes**:
- No pre-populated data in event store
- Ingest sidecar not running
- Address not yet indexed

**Solutions**:
1. Start with ingest overlay:
```bash
docker compose -f docker-compose.graph.yml -f docker-compose.graph.ingest.yml up -d --build
```

2. Load test data:
```bash
python scripts/dev/load_perf_fixture_dataset.py
```

3. Verify ingest status:
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" http://localhost:8081/api/v1/status
```

### Performance Issues

**Slow Graph Expansion**
**Symptoms**: Expansion requests taking >10 seconds
**Causes**:
- Large Neo4j queries without proper indexing
- Redis cache misses
- Network latency to RPC endpoints

**Solutions**:
1. Check Neo4j query performance:
```bash
docker exec jackdawsentry_graph_neo4j cypher-shell -u neo4j -p PASSWORD
```

2. Monitor Redis hit rates:
```bash
docker exec jackdawsentry_graph_redis redis-cli monitor
```

3. Verify RPC endpoint health:
```bash
curl -s https://ethereum-rpc.publicnode.com/health
```

### Memory Issues

**Container Restarting**
**Symptoms**: Containers repeatedly restart, OOM errors
**Causes**:
- Insufficient Docker memory allocation
- Large graph expansions exceeding limits

**Solutions**:
1. Increase Docker memory limits in docker-compose.yml:
```yaml
services:
  graph-api:
    deploy:
      resources:
        limits:
          memory: 4G
```

2. Add Neo4j memory configuration:
```yaml
services:
  neo4j:
    environment:
      NEO4J_dbms_memory_heap_initial_size: 512m
      NEO4J_dbms_memory_heap_max_size: 2G
```

### Database Issues

**Connection Refused**
**Problem**: Services can't connect to databases
**Solutions**:
1. Verify database containers are running:
```bash
docker compose -f docker-compose.graph.yml ps
```

2. Check database logs:
```bash
docker compose -f docker-compose.graph.yml logs neo4j
docker compose -f docker-compose.graph.yml logs postgres
docker compose -f docker-compose.graph.yml logs redis
```

3. Manual database connectivity test:
```bash
# Test Neo4j
docker exec jackdawsentry_graph_api python -c "
from src.api.database import get_neo4j_session
async def test():
    async with get_neo4j_session() as session:
        result = await session.run('RETURN 1')
        print('Neo4j OK:', result)

import asyncio
asyncio.run(test())
"
```

### Frontend Issues

**Blank Page or Loading Error**
**Problem**: Frontend fails to load or shows blank page
**Solutions**:
1. Check API connectivity:
```bash
curl http://localhost:8081/health
```

2. Verify nginx configuration:
```bash
docker exec jackdawsentry_graph_nginx nginx -t
```

3. Check browser console for CORS errors
4. Verify frontend build completed successfully

**Graph Not Updating**
**Problem**: Expanding nodes doesn't update the graph visualization
**Causes**:
- WebSocket connection issues
- Frontend not processing ExpansionResponse v2 format
- Branch ID conflicts

**Solutions**:
1. Check browser network tab for WebSocket errors
2. Clear browser cache and localStorage
3. Verify API responses match expected format
4. Check for branch ID conflicts in session

### Development Issues

**Code Changes Not Reflected**
**Problem**: Modified source code not reflected in running container
**Solutions**:
1. Rebuild with --no-cache:
```bash
docker compose -f docker-compose.graph.yml build --no-cache
```

2. Force recreate containers:
```bash
docker compose -f docker-compose.graph.yml up --force-recreate
```

**Test Failures**
**Problem**: Tests failing with import errors
**Solutions**:
1. Install test dependencies:
```bash
pip install -r requirements-test.txt
```

2. Run specific test modules:
```bash
pytest tests/test_trace_compiler -v
pytest tests/test_api -v
```

### Debugging Tools

**Enable Debug Logging**
```bash
# Add to .env
LOG_LEVEL=DEBUG

# Restart services
docker compose -f docker-compose.graph.yml restart
```

**Access Container Shells**
```bash
# Graph API container
docker exec -it jackdawsentry_graph_api bash

# Database containers
docker exec -it jackdawsentry_graph_neo4j cypher-shell
docker exec -it jackdawsentry_graph_postgres psql -U jackdawsentry_user -d jackdawsentry_graph
```

**Monitor Resource Usage**
```bash
# Container resource usage
docker stats

# Database sizes
docker exec jackdawsentry_graph_neo4j du -sh /data
docker exec jackdawsentry_graph_postgres du -sh /var/lib/postgresql/data
```

## Getting Help

**Collect Debug Information**
When reporting issues, include:
1. Environment variables (sanitized)
2. Container logs from all services
3. Browser console errors
4. Network request/response details
5. Steps to reproduce

**Log Collection Commands**
```bash
# Save all service logs
docker compose -f docker-compose.graph.yml logs > debug-logs.txt

# Save specific service logs
docker compose -f docker-compose.graph.yml logs graph-api > api-logs.txt
```

**Support Channels**
- GitHub Issues: https://github.com/storagebirddrop/jackdawsentry-graph/issues
- Documentation: Check `/docs/` directory for detailed guides
- Security Issues: Follow SECURITY.md reporting guidelines
