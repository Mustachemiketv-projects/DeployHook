"""Session-based auth helpers for the web UI."""
import os
from functools import wraps
from typing import Callable

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-secret")


def check_credentials(username: str, password: str) -> bool:
    from app.models import verify_user
    return verify_user(username, password)


def is_logged_in(request: Request) -> bool:
    return request.session.get("authenticated") is True


def require_login(func: Callable):
    """Decorator for UI route handlers — redirects to /login if not authed."""
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_logged_in(request):
            return RedirectResponse("/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper
