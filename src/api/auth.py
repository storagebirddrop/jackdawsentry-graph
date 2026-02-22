"""
Jackdaw Sentry - Authentication Module
JWT-based authentication with GDPR compliance
"""

import uuid
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import logging

from src.api.config import settings

logger = logging.getLogger(__name__)

# JWT Bearer token scheme
security = HTTPBearer()


class User(BaseModel):
    """User model for authentication"""
    id: UUID
    username: str
    email: str
    role: str = "viewer"
    permissions: List[str]
    is_active: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None


class TokenData(BaseModel):
    """Token data model"""
    user_id: Optional[str] = None
    username: Optional[str] = None
    permissions: List[str] = []
    exp: Optional[datetime] = None


class LoginRequest(BaseModel):
    """Login request model"""
    username: str
    password: str


class TokenResponse(BaseModel):
    """Token response model"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict


# =============================================================================
# Password hashing
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash"""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


# =============================================================================
# JWT token management
# =============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "iat": now})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    logger.info(f"Created access token for user: {data.get('sub', 'unknown')}")
    return encoded_jwt


def verify_token(token: str) -> TokenData:
    """Verify JWT token and return token data"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        username: str = payload.get("sub")
        user_id: str = payload.get("user_id")
        permissions: List[str] = payload.get("permissions", [])
        exp: datetime = datetime.fromtimestamp(payload.get("exp"), tz=timezone.utc)
        
        if username is None:
            raise credentials_exception
            
        return TokenData(user_id=user_id, username=username, permissions=permissions, exp=exp)
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise credentials_exception
    except jwt.PyJWTError as e:
        logger.warning(f"JWT validation error: {e}")
        raise credentials_exception
    except (KeyError, ValueError, TypeError) as e:
        logger.warning(f"Token payload parsing error: {e}")
        raise credentials_exception


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """Get current authenticated user from token, backed by database lookup"""
    token_data = verify_token(credentials.credentials)
    
    try:
        from src.api.database import get_postgres_connection
        
        async with get_postgres_connection() as conn:
            row = await conn.fetchrow(
                "SELECT id, username, email, role, is_active, created_at, last_login "
                "FROM users WHERE username = $1",
                token_data.username
            )
        
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        role = row["role"]
        permissions = ROLES.get(role, ROLES["viewer"])
        
        user = User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            role=role,
            permissions=permissions,
            is_active=row["is_active"],
            created_at=row["created_at"],
            last_login=row["last_login"]
        )
        
        if not user.is_active:
            raise HTTPException(status_code=400, detail="Inactive user")
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error during user lookup: {e}")
        # Fallback: construct user from token data if DB is unavailable
        return User(
            id=UUID(token_data.user_id) if token_data.user_id else uuid.uuid4(),
            username=token_data.username,
            email=f"{token_data.username}@jackdawsentry.com",
            role="viewer",
            permissions=token_data.permissions,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc)
        )


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user (checks if user is active)"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def check_permissions(required_permissions: List[str]):
    """Decorator to check if user has required permissions"""
    def permission_checker(current_user: User = Depends(get_current_active_user)):
        user_permissions = set(current_user.permissions)
        required = set(required_permissions)
        
        if not required.issubset(user_permissions):
            missing = required - user_permissions
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permissions: {', '.join(missing)}"
            )
        
        return current_user
    
    return permission_checker


async def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Require admin-level access (admin:system permission)"""
    if "admin:system" not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# Permission constants
PERMISSIONS = {
    "read_analysis": "analysis:read",
    "write_analysis": "analysis:write",
    "read_compliance": "compliance:read",
    "write_compliance": "compliance:write",
    "read_investigations": "investigations:read",
    "write_investigations": "investigations:write",
    "read_blockchain": "blockchain:read",
    "write_blockchain": "blockchain:write",
    "read_intelligence": "intelligence:read",
    "write_intelligence": "intelligence:write",
    "read_reports": "reports:read",
    "write_reports": "reports:write",
    "read_attribution": "attribution:read",
    "write_attribution": "attribution:write",
    "bulk_screening": "attribution:bulk",
    "view_analytics": "analytics:view",
    "admin_full": "admin:full",
    "admin_users": "admin:users",
    "admin_system": "admin:system"
}


# Role definitions
ROLES = {
    "viewer": [
        PERMISSIONS["read_analysis"],
        PERMISSIONS["read_compliance"],
        PERMISSIONS["read_investigations"],
        PERMISSIONS["read_blockchain"],
        PERMISSIONS["read_intelligence"],
        PERMISSIONS["read_reports"],
        PERMISSIONS["read_attribution"]
    ],
    "analyst": [
        PERMISSIONS["read_analysis"],
        PERMISSIONS["write_analysis"],
        PERMISSIONS["read_compliance"],
        PERMISSIONS["read_investigations"],
        PERMISSIONS["write_investigations"],
        PERMISSIONS["read_blockchain"],
        PERMISSIONS["read_intelligence"],
        PERMISSIONS["read_reports"],
        PERMISSIONS["write_reports"],
        PERMISSIONS["read_attribution"],
        PERMISSIONS["write_attribution"]
    ],
    "compliance_officer": [
        PERMISSIONS["read_analysis"],
        PERMISSIONS["read_compliance"],
        PERMISSIONS["write_compliance"],
        PERMISSIONS["read_investigations"],
        PERMISSIONS["write_investigations"],
        PERMISSIONS["read_blockchain"],
        PERMISSIONS["read_intelligence"],
        PERMISSIONS["read_reports"],
        PERMISSIONS["write_reports"],
        PERMISSIONS["read_attribution"],
        PERMISSIONS["write_attribution"],
        PERMISSIONS["bulk_screening"],
        PERMISSIONS["view_analytics"]
    ],
    "admin": list(PERMISSIONS.values())
}


async def get_user_permissions(username: str) -> List[str]:
    """Get user permissions from database role"""
    try:
        from src.api.database import get_postgres_connection
        
        async with get_postgres_connection() as conn:
            role = await conn.fetchval(
                "SELECT role FROM users WHERE username = $1", username
            )
        
        return ROLES.get(role, ROLES["viewer"])
    except Exception as e:
        logger.error(f"Failed to get permissions for {username}: {e}")
        return ROLES["viewer"]


async def authenticate_user(username: str, password: str) -> Optional[User]:
    """Authenticate user credentials against database"""
    try:
        from src.api.database import get_postgres_connection
        
        async with get_postgres_connection() as conn:
            row = await conn.fetchrow(
                "SELECT id, username, email, password_hash, role, is_active, "
                "created_at, last_login FROM users WHERE username = $1",
                username
            )
        
        if row is None:
            return None
        
        if not verify_password(password, row["password_hash"]):
            return None
        
        if not row["is_active"]:
            return None
        
        role = row["role"]
        permissions = ROLES.get(role, ROLES["viewer"])
        
        # Update last_login
        async with get_postgres_connection() as conn:
            await conn.execute(
                "UPDATE users SET last_login = $1 WHERE id = $2",
                datetime.now(timezone.utc), row["id"]
            )
        
        user = User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            role=role,
            permissions=permissions,
            is_active=row["is_active"],
            created_at=row["created_at"],
            last_login=datetime.now(timezone.utc)
        )
        
        logger.info(f"User authenticated: {username}")
        return user
        
    except Exception as e:
        logger.error(f"Authentication error: {e}")
        return None


def create_user_token(user: User) -> str:
    """Create access token for user"""
    token_data = {
        "sub": user.username,
        "user_id": str(user.id),
        "permissions": user.permissions,
    }
    
    return create_access_token(token_data)


# =============================================================================
# GDPR compliance functions
# =============================================================================

def log_access_attempt(username: str, success: bool, ip_address: str = None):
    """Log access attempt for GDPR compliance"""
    log_data = {
        "username": username,
        "success": success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ip_address": ip_address
    }
    
    if success:
        logger.info(f"Successful login: {log_data}")
    else:
        logger.warning(f"Failed login attempt: {log_data}")


async def get_user_data_export(username: str) -> Dict:
    """Get user data for GDPR export from database"""
    try:
        from src.api.database import get_postgres_connection
        
        async with get_postgres_connection() as conn:
            row = await conn.fetchrow(
                "SELECT username, email, role, created_at, last_login, "
                "gdpr_consent_given, gdpr_consent_date FROM users WHERE username = $1",
                username
            )
        
        if row:
            return {
                "username": row["username"],
                "email": row["email"],
                "role": row["role"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "data_types": ["authentication_logs", "access_logs", "permissions"],
                "export_date": datetime.now(timezone.utc).isoformat(),
                "data_retention_days": settings.DATA_RETENTION_DAYS
            }
    except Exception as e:
        logger.error(f"GDPR export error for {username}: {e}")
    
    return {
        "username": username,
        "data_types": ["authentication_logs", "access_logs", "permissions"],
        "export_date": datetime.now(timezone.utc).isoformat(),
        "data_retention_days": settings.DATA_RETENTION_DAYS
    }


async def delete_user_data(username: str) -> bool:
    """Delete user data for GDPR compliance"""
    try:
        from src.api.database import get_postgres_connection
        
        async with get_postgres_connection() as conn:
            result = await conn.execute(
                "UPDATE users SET is_active = false, email = 'deleted@gdpr.local', "
                "password_hash = 'DELETED' WHERE username = $1",
                username
            )
        
        logger.info(f"GDPR data deletion completed for user: {username}")
        return True
    except Exception as e:
        logger.error(f"GDPR deletion error for {username}: {e}")
        return False
