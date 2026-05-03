"""
Plain-password auth for Canvas Homes dashboard.

Users defined in this file (or USERS_JSON env var if you prefer).
Sessions are signed cookies via itsdangerous.

Upgrade path: when you have >5 paying users, swap _verify_password for
bcrypt and load users from a database. That's a 30-minute upgrade.
"""
import os
import json
from typing import Optional
from itsdangerous import URLSafeSerializer, BadSignature
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse


# ── Hardcoded users (move to env var or DB later) ──────────────────────
USERS = {
    "shreyas":  {"password": "shrey2026!",  "role": "admin",  "name": "Shreyas"},
    "editor1":  {"password": "editor2026!", "role": "editor", "name": "Editor One"},
    "editor2":  {"password": "editor2026!", "role": "editor", "name": "Editor Two"},
}
# Override from env if present (lets you change pwds in deploy without redeploy)
if os.getenv("USERS_JSON"):
    try:
        USERS = json.loads(os.environ["USERS_JSON"])
    except Exception as e:
        print(f"USERS_JSON env var present but invalid: {e}")

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production-please")
SESSION_COOKIE = "canvas_session"
serializer = URLSafeSerializer(SESSION_SECRET, salt="canvas-auth")


def login(username: str, password: str) -> Optional[dict]:
    user = USERS.get(username)
    if not user or user["password"] != password:
        return None
    return {"username": username, "role": user["role"], "name": user["name"]}


def make_session_cookie(user: dict) -> str:
    return serializer.dumps(user)


def read_session_cookie(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        return serializer.loads(token)
    except BadSignature:
        return None


def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    return read_session_cookie(token) if token else None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"location": "/login"})
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user