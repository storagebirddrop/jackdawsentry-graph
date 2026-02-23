"""
Jackdaw Sentry - Authentication Router
Login, token refresh, and user management endpoints
"""

import logging

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import status

from src.api.auth import LoginRequest
from src.api.auth import TokenResponse
from src.api.auth import authenticate_user
from src.api.auth import create_user_token
from src.api.auth import log_access_attempt
from src.api.auth import settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, login_data: LoginRequest):
    """Authenticate user and return JWT token"""
    client_ip = request.headers.get(
        "X-Forwarded-For", request.client.host if request.client else "unknown"
    )

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
