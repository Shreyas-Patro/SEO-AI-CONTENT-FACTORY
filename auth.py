"""
auth.py — minimal multi-user auth for Canvas Homes.

Design:
    - Hardcoded user list (USERS dict). Easy to read, easy to rotate.
    - Itsdangerous-signed session cookie carrying the username.
    - Two roles: "admin" and "user". Admins see /admin, can manage API keys,
      and see all users' runs. Users see only their own runs (filtered in
      the route handlers).
    - Three FastAPI dependencies: require_user, require_admin, optional_user.
    - The exact import surface app.py already expects:
          SESSION_COOKIE, USERS, current_user, login,
          make_session_cookie, require_admin, require_user

Hardening checklist for production:
    - Replace plaintext passwords with hashed (bcrypt) values. The verify
      function below already routes through `_verify_password`; just swap
      the implementation.
    - Set SESSION_SECRET from .env, never commit a real one.
    - Use HTTPS in production so the session cookie can be Secure-flagged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

# ─── Config ───────────────────────────────────────────────────────────────

# Cookie name. Keep stable so existing sessions survive deploys.
SESSION_COOKIE = "BLOG-AI_session"

# Secret used to sign the session cookie. Override via env var in prod.
# If you change this, every existing session is invalidated (users get
# logged out and have to sign in again).
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-secret-change-me-in-prod")

_serializer = URLSafeSerializer(SESSION_SECRET, salt="BLOG-AI-session")


# ─── Users (hardcoded — replace with bcrypt-hashed values in production) ──
# Each entry:
#   username -> {
#       password: <plaintext now, bcrypt later>,
#       name:     display name shown in the UI,
#       role:     "admin" | "user",
#       email:    optional, surfaces on admin page
#   }
#
# To add or remove team members, edit this dict and restart the server.
USERS: dict[str, dict] = {
    "admin": {
        "password": "canvashomes",
        "name": "Shreyas",
        "role": "admin",
        "email": "shreyas@canvas-homes.com",
    },
    "writer1": {
        "password": "change-me-writer1",
        "name": "Writer One",
        "role": "user",
        "email": "writer1@canvas-homes.com",
    },
    "writer2": {
        "password": "change-me-writer2",
        "name": "Writer Two",
        "role": "user",
        "email": "writer2@canvas-homes.com",
    },
    "reviewer": {
        "password": "change-me-reviewer",
        "name": "Reviewer",
        "role": "user",
        "email": "reviewer@canvas-homes.com",
    },
}


# ─── Password verification (swap to bcrypt in production) ─────────────────

def _verify_password(plaintext: str, stored: str) -> bool:
    """
    Constant-time comparison. Plain string equality is fine for a single-
    machine internal tool but use bcrypt/argon2 the moment this is exposed
    beyond your team.
    """
    return hmac.compare_digest(plaintext, stored)


# ─── Public API ───────────────────────────────────────────────────────────

def login(username: str, password: str) -> Optional[dict]:
    """
    Returns the user record (with username injected) if credentials check out,
    or None otherwise. The returned dict is what's stored in request.state
    by the dependencies below.
    """
    if not username or not password:
        return None
    user = USERS.get(username.strip().lower())
    if not user:
        return None
    if not _verify_password(password, user["password"]):
        return None
    return {
        "username": username.strip().lower(),
        "name": user["name"],
        "role": user["role"],
        "email": user.get("email", ""),
    }


def make_session_cookie(user: dict) -> str:
    """Sign and return the value to put in the SESSION_COOKIE."""
    payload = {"username": user["username"]}
    return _serializer.dumps(payload)


def _read_session_cookie(value: str) -> Optional[dict]:
    """Verify a cookie and return the user record, or None if invalid."""
    if not value:
        return None
    try:
        payload = _serializer.loads(value)
    except BadSignature:
        return None
    username = (payload or {}).get("username")
    if not username:
        return None
    user = USERS.get(username)
    if not user:
        # User was removed from USERS — invalidate session.
        return None
    return {
        "username": username,
        "name": user["name"],
        "role": user["role"],
        "email": user.get("email", ""),
    }


def current_user(request: Request) -> Optional[dict]:
    """
    Look up the user from the session cookie. Returns None if not signed in
    or cookie is invalid. Use this when you want to know the user but don't
    want to force a redirect.
    """
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return _read_session_cookie(cookie)


# ─── FastAPI dependencies ─────────────────────────────────────────────────

def require_user(request: Request) -> dict:
    """
    Dependency: require an authenticated user. Raises 401 (which our auth
    middleware can convert to a redirect) if absent.
    """
    user = current_user(request)
    if not user:
        # Returning a 401 rather than redirecting keeps API clients sane.
        # The outermost middleware in app.py turns this into a /login
        # redirect when the request is HTML.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required",
            headers={"Location": "/login"},
        )
    return user


def require_admin(request: Request) -> dict:
    """Dependency: require an authenticated user with role=admin."""
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def optional_user(request: Request) -> Optional[dict]:
    """Dependency: returns the user if signed in, None if not. Doesn't raise."""
    return current_user(request)


# ─── Helpers ──────────────────────────────────────────────────────────────

def is_admin(user: Optional[dict]) -> bool:
    return bool(user) and user.get("role") == "admin"


def list_users() -> list[dict]:
    """Used by the admin page to render the team table."""
    return [
        {
            "username": u,
            "name": data["name"],
            "role": data["role"],
            "email": data.get("email", ""),
        }
        for u, data in USERS.items()
    ]