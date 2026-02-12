"""
Jackdaw Sentry - Authentication Router
Login, token refresh, and user management endpoints
"""

from fastapi import APIRouter, HTTPException, Request, status
from datetime import timezone, datetime
import logging

from src.api.auth import (
    LoginRequest,
    TokenResponse,
    authenticate_user,
    create_user_token,
    log_access_attempt,
    hash_password,
    settings,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, login_data: LoginRequest):
    """Authenticate user and return JWT token"""
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")

    user = await authenticate_user(login_data.username, login_data.password)

    if user is None:
        log_access_attempt(login_data.username, success=False, ip_address=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    log_access_attempt(user.username, success=True, ip_address=client_ip)

    access_token = create_user_token(user)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRE_MINUTES * 60,
        user={
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role,
        },
    )
