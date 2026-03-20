"""
Jackdaw Sentry - Middleware Components
Security, audit, and rate limiting middleware
"""

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import time
from collections import defaultdict
from collections import deque
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import aiofiles
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from src.api.config import settings
from src.api.database import get_redis_connection


def _is_valid_ip(value: str) -> bool:
    """Check whether *value* is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


def get_client_ip(request: Request, trust_proxy_headers: bool = None) -> str:
    """Extract client IP from request, optionally checking proxy headers.

    If *trust_proxy_headers* is ``None`` the value is read from
    ``settings.TRUST_PROXY_HEADERS``.
    """
    if trust_proxy_headers is None:
        trust_proxy_headers = settings.TRUST_PROXY_HEADERS

    if trust_proxy_headers:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            candidate = forwarded_for.split(",")[0].strip()
            if _is_valid_ip(candidate):
                return candidate

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            candidate = real_ip.strip()
            if _is_valid_ip(candidate):
                return candidate

    host = request.client.host if request.client else None
    if host and _is_valid_ip(host):
        return host

    return "unknown"


logger = logging.getLogger(__name__)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Security middleware for request validation and protection"""

    def __init__(self, app, **kwargs):
        super().__init__(app)
        self.blocked_ips = set()
        self.suspicious_requests = defaultdict(int)
        self.max_requests_per_minute = kwargs.get("max_requests_per_minute", 1000)
        self.block_duration_minutes = kwargs.get("block_duration_minutes", 60)

        # Load blocked IPs from configuration
        self._load_blocked_ips()

    def _load_blocked_ips(self):
        """Load blocked IPs from configuration"""
        # In production, this would load from database or config file
        # For now, we'll use a placeholder
        self.blocked_ips = set()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request through security checks"""
        if os.environ.get("TESTING"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        # Check if IP is blocked
        if client_ip in self.blocked_ips:
            logger.warning(f"Blocked IP attempted access: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )

        # Validate request headers
        validation_result = self._validate_request(request)
        if not validation_result["valid"]:
            logger.warning(f"Request validation failed: {validation_result['reason']}")
            self._handle_suspicious_request(client_ip)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request"
            )

        # Add security headers
        response = await call_next(request)
        self._add_security_headers(response)

        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        return get_client_ip(request)

    def _validate_request(self, request: Request) -> Dict[str, Any]:
        """Validate request for security issues"""
        # Check request size
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > 10 * 1024 * 1024:  # 10MB limit
            return {"valid": False, "reason": "Request too large"}

        # Check user agent
        user_agent = request.headers.get("user-agent", "")
        if not user_agent or len(user_agent) < 10:
            return {"valid": False, "reason": "Invalid user agent"}

        # Check for suspicious patterns
        suspicious_patterns = [
            "<script",
            "javascript:",
            "vbscript:",
            "onload=",
            "onerror=",
            "eval(",
            "alert(",
            "document.cookie",
            "window.location",
        ]

        url = str(request.url).lower()
        for pattern in suspicious_patterns:
            if pattern in url:
                return {
                    "valid": False,
                    "reason": f"Suspicious pattern detected: {pattern}",
                }

        return {"valid": True, "reason": None}

    def _handle_suspicious_request(self, client_ip: str):
        """Handle suspicious request from IP"""
        self.suspicious_requests[client_ip] += 1

        # Block IP if too many suspicious requests
        if self.suspicious_requests[client_ip] > 10:
            self.blocked_ips.add(client_ip)
            logger.warning(f"IP blocked due to suspicious activity: {client_ip}")

            # Schedule unblocking
            asyncio.create_task(self._unblock_ip_later(client_ip))

    async def _unblock_ip_later(self, client_ip: str):
        """Unblock IP after duration"""
        await asyncio.sleep(self.block_duration_minutes * 60)
        self.blocked_ips.discard(client_ip)
        self.suspicious_requests[client_ip] = 0
        logger.info(f"IP unblocked: {client_ip}")

    def _add_security_headers(self, response: Response):
        """Add security headers to response"""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"


class AuditMiddleware(BaseHTTPMiddleware):
    """Audit middleware for logging and compliance"""

    def __init__(self, app, **kwargs):
        super().__init__(app)
        self.log_sensitive_data = kwargs.get("log_sensitive_data", False)
        self.audit_retention_days = kwargs.get("audit_retention_days", 2555)  # 7 years

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request through audit logging"""
        if os.environ.get("TESTING"):
            return await call_next(request)

        start_time = time.time()

        # Generate request ID
        request_id = self._generate_request_id()

        # Log request
        request_log = await self._log_request(request, request_id)

        # Process request
        try:
            response = await call_next(request)

            # Log response
            response_log = await self._log_response(response, request_id, start_time)

            # Store audit log
            await self._store_audit_log(request_log, response_log)

            return response

        except Exception as e:
            # Log error
            error_log = await self._log_error(e, request_id, start_time)
            await self._store_audit_log(request_log, error_log)
            raise

    def _generate_request_id(self) -> str:
        """Generate unique request ID"""
        timestamp = str(int(time.time() * 1000))
        random_hash = hashlib.sha256(
            f"{timestamp}{time.time_ns()}".encode()
        ).hexdigest()[:8]
        return f"REQ-{timestamp}-{random_hash}"

    async def _log_request(self, request: Request, request_id: str) -> Dict[str, Any]:
        """Log request details"""
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")

        # Get user info if available (would need to be passed from auth middleware)
        user_info = getattr(request.state, "user", None)

        request_log = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "content_length": request.headers.get("content-length"),
            "user_id": user_info.username if user_info else None,
            "user_permissions": user_info.permissions if user_info else None,
        }

        # Log sensitive data only if enabled
        if self.log_sensitive_data and request.method in ["POST", "PUT", "PATCH"]:
            try:
                body = await request.body()
                if body:
                    request_log["body_hash"] = hashlib.sha256(body).hexdigest()
            except Exception:
                pass

        logger.info(
            f"Request started: {request_id} - {request.method} {request.url.path}"
        )
        return request_log

    async def _log_response(
        self, response: Response, request_id: str, start_time: float
    ) -> Dict[str, Any]:
        """Log response details"""
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds

        response_log = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": response.status_code,
            "content_length": response.headers.get("content-length"),
            "processing_time_ms": round(processing_time, 2),
            "response_headers": dict(response.headers),
        }

        logger.info(
            f"Request completed: {request_id} - {response.status_code} in {processing_time:.2f}ms"
        )
        return response_log

    async def _log_error(
        self, error: Exception, request_id: str, start_time: float
    ) -> Dict[str, Any]:
        """Log error details"""
        processing_time = (time.time() - start_time) * 1000

        error_log = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "processing_time_ms": round(processing_time, 2),
            "stack_trace": str(error.__traceback__) if error.__traceback__ else None,
        }

        logger.error(
            f"Request failed: {request_id} - {type(error).__name__}: {str(error)}"
        )
        return error_log

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        return get_client_ip(request)

    @staticmethod
    def _json_default(obj):
        """Fallback serializer for types not natively supported by json.dumps."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        try:
            return str(obj)
        except Exception:
            return None

    async def _store_audit_log(
        self, request_log: Dict[str, Any], response_log: Dict[str, Any]
    ):
        """Store audit log in Redis, falling back to a local JSON-lines file."""
        audit_data = {
            "request": request_log,
            "response": response_log,
        }
        try:
            async with get_redis_connection() as redis:
                audit_key = f"audit:{request_log['request_id']}"
                await redis.setex(
                    audit_key,
                    self.audit_retention_days * 24 * 3600,
                    json.dumps(audit_data, default=self._json_default),
                )
        except Exception as e:
            logger.warning(f"Redis audit write failed, falling back to file: {e}")
            await self._store_audit_log_file(audit_data)

    async def _store_audit_log_file(self, audit_data: Dict[str, Any]):
        """Append an audit record to a local JSON-lines file (fallback)."""
        log_dir = os.environ.get("AUDIT_LOG_DIR", "/app/logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            async with aiofiles.open(
                os.path.join(log_dir, "audit_fallback.jsonl"), mode="a"
            ) as f:
                await f.write(json.dumps(audit_data, default=self._json_default) + "\n")
        except Exception as e:
            logger.error(f"Audit fallback file write also failed: {e}")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed rate limiting middleware with local fallback."""

    def __init__(self, app, **kwargs):
        super().__init__(app)
        self.default_requests_per_minute = kwargs.get(
            "requests_per_minute",
            settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
        )
        self.local_rate_limits = defaultdict(deque)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process request through rate limiting."""
        if os.environ.get("TESTING") or not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        user_key = self._extract_user_key(request)
        endpoint_label, specs = self._build_rate_limit_specs(request, client_ip, user_key)

        exceeded_spec = await self._check_rate_limits(endpoint_label, specs)
        if exceeded_spec is not None:
            scope, actor, limit, window_seconds = exceeded_spec
            logger.warning(
                "Rate limit exceeded for endpoint=%s scope=%s actor=%s limit=%s window=%ss",
                endpoint_label,
                scope,
                actor,
                limit,
                window_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(window_seconds)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Endpoint"] = endpoint_label
        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        return get_client_ip(request)

    @staticmethod
    def _extract_user_key(request: Request) -> Optional[str]:
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return None

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            return None

        try:
            from src.api.auth import verify_token

            token_data = verify_token(token)
            return token_data.user_id or token_data.username
        except Exception:
            return None

    def _build_rate_limit_specs(
        self,
        request: Request,
        client_ip: str,
        user_key: Optional[str],
    ) -> tuple[str, List[tuple[str, str, int, int]]]:
        path = request.url.path.rstrip("/") or "/"
        method = request.method.upper()
        specs: List[tuple[str, str, int, int]] = []

        if method == "POST" and path == "/api/v1/auth/login":
            specs.extend(
                [
                    ("ip", client_ip, 5, 60),
                    ("ip", client_ip, 20, 3600),
                ]
            )
            return "auth_login", specs

        if method == "POST" and path == "/api/v1/graph/sessions":
            if user_key:
                specs.append(("user", user_key, 10, 60))
            specs.append(("ip", client_ip, 30, 60))
            return "graph_session_create", specs

        if method == "POST" and (
            path.endswith("/expand")
            or path in {
                "/api/v1/graph/expand",
                "/api/v1/graph/expand-bridge",
                "/api/v1/graph/expand-utxo",
                "/api/v1/graph/expand-solana-tx",
            }
        ):
            if user_key:
                specs.append(("user", user_key, 30, 60))
            specs.append(("ip", client_ip, 60, 60))
            return "graph_expand", specs

        if method == "GET" and "/api/v1/graph/sessions/" in path and path.endswith("/status"):
            if user_key:
                specs.append(("user", user_key, 60, 60))
            specs.append(("ip", client_ip, 120, 60))
            return "graph_hop_status", specs

        specs.append(("ip", client_ip, self.default_requests_per_minute, 60))
        return "default", specs

    async def _check_rate_limits(
        self,
        endpoint_label: str,
        specs: List[tuple[str, str, int, int]],
    ) -> Optional[tuple[str, str, int, int]]:
        for scope, actor, limit, window_seconds in specs:
            if not actor:
                continue

            allowed = await self._check_limit(endpoint_label, scope, actor, limit, window_seconds)
            if not allowed:
                return (scope, actor, limit, window_seconds)
        return None

    async def _check_limit(
        self,
        endpoint_label: str,
        scope: str,
        actor: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        try:
            async with get_redis_connection() as redis:
                return await self._check_limit_redis(
                    redis,
                    endpoint_label,
                    scope,
                    actor,
                    limit,
                    window_seconds,
                )
        except Exception as exc:
            logger.warning(
                "Redis-backed rate limiting unavailable for %s/%s: %s; using local fallback",
                endpoint_label,
                actor,
                exc,
            )
            return self._check_limit_local(
                endpoint_label,
                scope,
                actor,
                limit,
                window_seconds,
            )

    async def _check_limit_redis(
        self,
        redis,
        endpoint_label: str,
        scope: str,
        actor: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        bucket = int(time.time() // window_seconds)
        key = f"rate:{endpoint_label}:{scope}:{actor}:{window_seconds}:{bucket}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
        return int(count) <= limit

    def _check_limit_local(
        self,
        endpoint_label: str,
        scope: str,
        actor: str,
        limit: int,
        window_seconds: int,
    ) -> bool:
        now = time.time()
        key = f"{endpoint_label}:{scope}:{actor}:{window_seconds}"
        window = self.local_rate_limits[key]
        cutoff = now - window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True



# ---------------------------------------------------------------------------
# Graph Latency Middleware
# ---------------------------------------------------------------------------

# Redis key prefix for per-endpoint latency samples (sorted sets, score = ts).
_LATENCY_KEY_PREFIX = "metrics:graph_latency:"
# Retain samples for 1 hour (rolling window for percentile calculation).
_LATENCY_WINDOW_SECONDS = 3600
# Maximum samples kept per endpoint to bound memory.
_LATENCY_MAX_SAMPLES = 10_000


class GraphLatencyMiddleware(BaseHTTPMiddleware):
    """Record p50/p95/p99 latency for all /api/v1/graph/* endpoints.

    Each request that matches the prefix is timed and the duration (in
    milliseconds) is written to a per-endpoint Redis sorted set, using the
    current Unix timestamp as the score.  Expired entries are pruned on
    every write so the set remains a rolling 1-hour window.

    The ``X-Response-Time-Ms`` header is added to every matched response so
    clients can observe individual latencies.

    Metrics are readable via ``GET /api/v1/graph/latency`` (defined in the
    graph router).
    """

    _GRAPH_PREFIX = "/api/v1/graph"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Time graph API requests and store samples in Redis."""
        if not request.url.path.startswith(self._GRAPH_PREFIX):
            return await call_next(request)

        t_start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"

        # Derive a short endpoint label: strip prefix, collapse path params.
        label = request.url.path[len(self._GRAPH_PREFIX):]
        if not label:
            label = "/"

        asyncio.create_task(
            self._record(label, elapsed_ms)
        )
        return response

    async def _record(self, endpoint: str, elapsed_ms: float) -> None:
        """Append one latency sample to the Redis sorted set for *endpoint*."""
        redis_key = f"{_LATENCY_KEY_PREFIX}{endpoint}"
        now = time.time()
        cutoff = now - _LATENCY_WINDOW_SECONDS

        try:
            async with get_redis_connection() as redis:
                # score = current timestamp; member = "<ts>:<ms>" (unique enough)
                member = f"{now:.3f}:{elapsed_ms:.2f}"
                await redis.zadd(redis_key, {member: now})
                # Prune samples older than the rolling window.
                await redis.zremrangebyscore(redis_key, "-inf", cutoff)
                # Cap total samples to avoid unbounded growth.
                count = await redis.zcard(redis_key)
                if count > _LATENCY_MAX_SAMPLES:
                    excess = count - _LATENCY_MAX_SAMPLES
                    await redis.zpopmin(redis_key, excess)
                await redis.expire(redis_key, _LATENCY_WINDOW_SECONDS + 60)
        except Exception as exc:
            logger.debug("GraphLatencyMiddleware._record failed: %s", exc)


async def get_graph_latency_stats() -> Dict[str, Any]:
    """Compute p50/p95/p99 for all graph endpoints from Redis sorted sets.

    Returns a dict keyed by endpoint label, each value containing p50, p95,
    p99 (milliseconds), sample_count, and window_seconds.

    Called from ``GET /api/v1/graph/latency``.
    """
    import statistics

    results: Dict[str, Any] = {}
    try:
        async with get_redis_connection() as redis:
            keys = await redis.keys(f"{_LATENCY_KEY_PREFIX}*")
            prefix_len = len(_LATENCY_KEY_PREFIX)
            now = time.time()
            cutoff = now - _LATENCY_WINDOW_SECONDS

            for raw_key in keys:
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                endpoint = key[prefix_len:]

                # Fetch all members in the rolling window.
                members = await redis.zrangebyscore(key, cutoff, "+inf")
                samples = []
                for m in members:
                    m_str = m.decode() if isinstance(m, bytes) else m
                    # member format: "<ts>:<ms>"
                    try:
                        ms = float(m_str.split(":", 1)[1])
                        samples.append(ms)
                    except (IndexError, ValueError):
                        pass

                if not samples:
                    continue

                samples_sorted = sorted(samples)
                n = len(samples_sorted)

                def _percentile(sorted_data: list, pct: float) -> float:
                    idx = int(len(sorted_data) * pct / 100)
                    idx = min(idx, len(sorted_data) - 1)
                    return round(sorted_data[idx], 2)

                results[endpoint] = {
                    "p50_ms": _percentile(samples_sorted, 50),
                    "p95_ms": _percentile(samples_sorted, 95),
                    "p99_ms": _percentile(samples_sorted, 99),
                    "mean_ms": round(statistics.mean(samples), 2),
                    "sample_count": n,
                    "window_seconds": _LATENCY_WINDOW_SECONDS,
                }
    except Exception as exc:
        logger.warning("get_graph_latency_stats failed: %s", exc)

    return results


# Middleware factory functions
def create_security_middleware(**kwargs):
    """Create security middleware with configuration"""
    return SecurityMiddleware(**kwargs)


def create_audit_middleware(**kwargs):
    """Create audit middleware with configuration"""
    return AuditMiddleware(**kwargs)


def create_rate_limit_middleware(**kwargs):
    """Create rate limit middleware with configuration"""
    return RateLimitMiddleware(**kwargs)
