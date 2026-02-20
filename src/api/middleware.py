"""
Jackdaw Sentry - Middleware Components
Security, audit, and rate limiting middleware
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
from fastapi import Request, Response, HTTPException, status
from fastapi.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
import json
import hashlib
import ipaddress
from collections import defaultdict, deque
import asyncio
import aiofiles

from src.api.database import get_redis_connection
from src.api.config import settings


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
        self.max_requests_per_minute = kwargs.get('max_requests_per_minute', 1000)
        self.block_duration_minutes = kwargs.get('block_duration_minutes', 60)
        
        # Load blocked IPs from configuration
        self._load_blocked_ips()
    
    def _load_blocked_ips(self):
        """Load blocked IPs from configuration"""
        # In production, this would load from database or config file
        # For now, we'll use a placeholder
        self.blocked_ips = set()
    
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request through security checks"""
        client_ip = self._get_client_ip(request)
        
        # Check if IP is blocked
        if client_ip in self.blocked_ips:
            logger.warning(f"Blocked IP attempted access: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        
        # Validate request headers
        validation_result = self._validate_request(request)
        if not validation_result["valid"]:
            logger.warning(f"Request validation failed: {validation_result['reason']}")
            self._handle_suspicious_request(client_ip)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request"
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
            "<script", "javascript:", "vbscript:", "onload=", "onerror=",
            "eval(", "alert(", "document.cookie", "window.location"
        ]
        
        url = str(request.url).lower()
        for pattern in suspicious_patterns:
            if pattern in url:
                return {"valid": False, "reason": f"Suspicious pattern detected: {pattern}"}
        
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
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"


class AuditMiddleware(BaseHTTPMiddleware):
    """Audit middleware for logging and compliance"""
    
    def __init__(self, app, **kwargs):
        super().__init__(app)
        self.log_sensitive_data = kwargs.get('log_sensitive_data', False)
        self.audit_retention_days = kwargs.get('audit_retention_days', 2555)  # 7 years
        
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request through audit logging"""
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
        random_hash = hashlib.md5(f"{timestamp}{time.time_ns()}".encode()).hexdigest()[:8]
        return f"REQ-{timestamp}-{random_hash}"
    
    async def _log_request(self, request: Request, request_id: str) -> Dict[str, Any]:
        """Log request details"""
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        
        # Get user info if available (would need to be passed from auth middleware)
        user_info = getattr(request.state, 'user', None)
        
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
            "user_permissions": user_info.permissions if user_info else None
        }
        
        # Log sensitive data only if enabled
        if self.log_sensitive_data and request.method in ["POST", "PUT", "PATCH"]:
            try:
                body = await request.body()
                if body:
                    request_log["body_hash"] = hashlib.sha256(body).hexdigest()
            except Exception:
                pass
        
        logger.info(f"Request started: {request_id} - {request.method} {request.url.path}")
        return request_log
    
    async def _log_response(self, response: Response, request_id: str, start_time: float) -> Dict[str, Any]:
        """Log response details"""
        processing_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        response_log = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status_code": response.status_code,
            "content_length": response.headers.get("content-length"),
            "processing_time_ms": round(processing_time, 2),
            "response_headers": dict(response.headers)
        }
        
        logger.info(f"Request completed: {request_id} - {response.status_code} in {processing_time:.2f}ms")
        return response_log
    
    async def _log_error(self, error: Exception, request_id: str, start_time: float) -> Dict[str, Any]:
        """Log error details"""
        processing_time = (time.time() - start_time) * 1000
        
        error_log = {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "processing_time_ms": round(processing_time, 2),
            "stack_trace": str(error.__traceback__) if error.__traceback__ else None
        }
        
        logger.error(f"Request failed: {request_id} - {type(error).__name__}: {str(error)}")
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

    async def _store_audit_log(self, request_log: Dict[str, Any], response_log: Dict[str, Any]):
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
            async with aiofiles.open(os.path.join(log_dir, "audit_fallback.jsonl"), mode="a") as f:
                await f.write(json.dumps(audit_data, default=self._json_default) + "\n")
        except Exception as e:
            logger.error(f"Audit fallback file write also failed: {e}")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware"""
    
    def __init__(self, app, **kwargs):
        super().__init__(app)
        self.requests_per_minute = kwargs.get('requests_per_minute', 100)
        self.requests_per_hour = kwargs.get('requests_per_hour', 1000)
        self.requests_per_day = kwargs.get('requests_per_day', 10000)
        self.burst_size = kwargs.get('burst_size', 20)
        
        # In-memory rate limiting storage (in production, use Redis)
        self.rate_limits = defaultdict(lambda: {
            'minute': deque(),
            'hour': deque(),
            'day': deque()
        })
    
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process request through rate limiting"""
        # Skip rate limiting in test mode
        if os.environ.get("TESTING"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        user_info = getattr(request.state, 'user', None)
        
        # Use user ID for rate limiting if authenticated, otherwise IP
        rate_limit_key = user_info.username if user_info else client_ip
        
        # Check rate limits
        if not self._check_rate_limit(rate_limit_key):
            logger.warning(f"Rate limit exceeded for: {rate_limit_key}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded"
            )
        
        # Record request
        self._record_request(rate_limit_key)
        
        # Add rate limit headers
        response = await call_next(request)
        self._add_rate_limit_headers(response, rate_limit_key)
        
        return response
    
    @staticmethod
    def _get_client_ip(request: Request) -> str:
        return get_client_ip(request)
    
    def _check_rate_limit(self, key: str) -> bool:
        """Check if rate limit is exceeded"""
        now = time.time()
        limits = self.rate_limits[key]
        
        # Clean old requests
        self._cleanup_old_requests(limits, now)
        
        # Check minute limit
        if len(limits['minute']) >= self.requests_per_minute:
            return False
        
        # Check hour limit
        if len(limits['hour']) >= self.requests_per_hour:
            return False
        
        # Check day limit
        if len(limits['day']) >= self.requests_per_day:
            return False
        
        # Check burst limit
        recent_requests = [req_time for req_time in limits['minute'] if now - req_time < 10]
        if len(recent_requests) >= self.burst_size:
            return False
        
        return True
    
    def _cleanup_old_requests(self, limits: Dict[str, deque], now: float):
        """Remove old requests from rate limit tracking"""
        # Clean minute window (60 seconds)
        while limits['minute'] and now - limits['minute'][0] > 60:
            limits['minute'].popleft()
        
        # Clean hour window (3600 seconds)
        while limits['hour'] and now - limits['hour'][0] > 3600:
            limits['hour'].popleft()
        
        # Clean day window (86400 seconds)
        while limits['day'] and now - limits['day'][0] > 86400:
            limits['day'].popleft()
    
    def _record_request(self, key: str):
        """Record request for rate limiting"""
        now = time.time()
        limits = self.rate_limits[key]
        
        limits['minute'].append(now)
        limits['hour'].append(now)
        limits['day'].append(now)
    
    def _add_rate_limit_headers(self, response: Response, key: str):
        """Add rate limit headers to response"""
        limits = self.rate_limits[key]
        now = time.time()
        
        # Clean old requests first
        self._cleanup_old_requests(limits, now)
        
        # Calculate remaining requests
        remaining_minute = max(0, self.requests_per_minute - len(limits['minute']))
        remaining_hour = max(0, self.requests_per_hour - len(limits['hour']))
        remaining_day = max(0, self.requests_per_day - len(limits['day']))
        
        # Add headers
        response.headers["X-RateLimit-Limit-Minute"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining-Minute"] = str(remaining_minute)
        response.headers["X-RateLimit-Limit-Hour"] = str(self.requests_per_hour)
        response.headers["X-RateLimit-Remaining-Hour"] = str(remaining_hour)
        response.headers["X-RateLimit-Limit-Day"] = str(self.requests_per_day)
        response.headers["X-RateLimit-Remaining-Day"] = str(remaining_day)
        
        # Calculate reset times
        if limits['minute']:
            reset_minute = int(limits['minute'][0] + 60 - now)
        else:
            reset_minute = 60
        
        if limits['hour']:
            reset_hour = int(limits['hour'][0] + 3600 - now)
        else:
            reset_hour = 3600
        
        if limits['day']:
            reset_day = int(limits['day'][0] + 86400 - now)
        else:
            reset_day = 86400
        
        response.headers["X-RateLimit-Reset-Minute"] = str(reset_minute)
        response.headers["X-RateLimit-Reset-Hour"] = str(reset_hour)
        response.headers["X-RateLimit-Reset-Day"] = str(reset_day)


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
