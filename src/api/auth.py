"""
Jackdaw Sentry - Authentication Module
JWT-based authentication with GDPR compliance
"""

import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, List
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
    username: str
    email: str
    permissions: List[str]
    is_active: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None


class TokenData(BaseModel):
    """Token data model"""
    username: Optional[str] = None
    permissions: List[str] = []
    exp: Optional[datetime] = None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    
    logger.info(f"Created access token for user: {data.get('username', 'unknown')}")
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
        permissions: List[str] = payload.get("permissions", [])
        exp: datetime = datetime.fromtimestamp(payload.get("exp"))
        
        if username is None:
            raise credentials_exception
            
        token_data = TokenData(username=username, permissions=permissions, exp=exp)
        
        # Check if token is expired
        if datetime.utcnow() > exp:
            raise credentials_exception
            
        return token_data
        
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise credentials_exception
    except jwt.JWTError as e:
        logger.warning(f"JWT validation error: {e}")
        raise credentials_exception


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """Get current authenticated user from token"""
    token_data = verify_token(credentials.credentials)
    
    # In a real implementation, you would fetch user from database
    # For now, we'll create a mock user based on token data
    user = User(
        username=token_data.username,
        email=f"{token_data.username}@jackdawsentry.com",
        permissions=token_data.permissions,
        is_active=True,
        created_at=datetime.utcnow(),
        last_login=datetime.utcnow()
    )
    
    logger.info(f"Authenticated user: {user.username}")
    return user


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
        PERMISSIONS["read_reports"]
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
        PERMISSIONS["write_reports"]
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
        PERMISSIONS["write_reports"]
    ],
    "admin": list(PERMISSIONS.values())
}


def get_user_permissions(username: str) -> List[str]:
    """Get user permissions (mock implementation - in real app, fetch from database)"""
    # Mock implementation - in production, this would query the database
    if username == "admin":
        return ROLES["admin"]
    elif username == "analyst":
        return ROLES["analyst"]
    elif username == "compliance":
        return ROLES["compliance_officer"]
    else:
        return ROLES["viewer"]


def authenticate_user(username: str, password: str) -> Optional[User]:
    """Authenticate user credentials (mock implementation)"""
    # Mock implementation - in production, this would verify against database
    # For demo purposes, we'll accept any non-empty credentials
    if username and password:
        permissions = get_user_permissions(username)
        
        user = User(
            username=username,
            email=f"{username}@jackdawsentry.com",
            permissions=permissions,
            is_active=True,
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow()
        )
        
        logger.info(f"User authenticated: {username}")
        return user
    
    return None


def create_user_token(user: User) -> str:
    """Create access token for user"""
    token_data = {
        "sub": user.username,
        "permissions": user.permissions,
        "iat": datetime.utcnow()
    }
    
    return create_access_token(token_data)


# GDPR compliance functions
def log_access_attempt(username: str, success: bool, ip_address: str = None):
    """Log access attempt for GDPR compliance"""
    log_data = {
        "username": username,
        "success": success,
        "timestamp": datetime.utcnow().isoformat(),
        "ip_address": ip_address
    }
    
    if success:
        logger.info(f"Successful login: {log_data}")
    else:
        logger.warning(f"Failed login attempt: {log_data}")


def get_user_data_export(username: str) -> Dict:
    """Get user data for GDPR export"""
    # Mock implementation - in production, this would collect all user data
    return {
        "username": username,
        "data_types": ["authentication_logs", "access_logs", "permissions"],
        "export_date": datetime.utcnow().isoformat(),
        "data_retention_days": settings.DATA_RETENTION_DAYS
    }


def delete_user_data(username: str) -> bool:
    """Delete user data for GDPR compliance"""
    # Mock implementation - in production, this would delete all user data
    logger.info(f"GDPR data deletion requested for user: {username}")
    return True
