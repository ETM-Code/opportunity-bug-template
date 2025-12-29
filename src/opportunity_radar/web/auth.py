"""Simple password authentication for the web interface."""

import hashlib
import os
import secrets
from functools import wraps

from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Routes that don't require authentication
PUBLIC_ROUTES = {"/health", "/login", "/static"}

# Session cookie name
SESSION_COOKIE = "opportunitybug_session"

# Secret key for signing cookies (generated once per deployment)
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))


def verify_password(password: str) -> bool:
    """Check if the provided password matches AUTH_PASSWORD."""
    expected = os.environ.get("AUTH_PASSWORD")
    if not expected:
        # No password set - allow access (for local dev)
        return True
    return secrets.compare_digest(password, expected)


def create_session_token() -> str:
    """Create a signed session token."""
    # Simple hash of secret key - valid for this deployment
    return hashlib.sha256(SECRET_KEY.encode()).hexdigest()[:32]


def verify_session_token(token: str) -> bool:
    """Verify a session token is valid."""
    expected = create_session_token()
    return secrets.compare_digest(token, expected)


def is_authenticated(request: Request) -> bool:
    """Check if the request has a valid session."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    return verify_session_token(token)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce authentication on protected routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow public routes
        if path in PUBLIC_ROUTES or any(path.startswith(r) for r in PUBLIC_ROUTES):
            return await call_next(request)

        # Check if AUTH_PASSWORD is set
        if not os.environ.get("AUTH_PASSWORD"):
            # No auth configured - allow all (local dev mode)
            return await call_next(request)

        # Check authentication
        if not is_authenticated(request):
            # API requests get 401, page requests get redirected
            if path.startswith("/api/"):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"}
                )
            return RedirectResponse(url="/login", status_code=302)

        return await call_next(request)
