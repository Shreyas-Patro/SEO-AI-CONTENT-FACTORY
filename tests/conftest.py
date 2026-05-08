"""
tests/conftest.py
-----------------------------------------------------------------------------
Fixed: doesn't depend on what passwords are in your real auth.USERS.
Instead, it injects two known test users into auth.USERS at fixture setup
time, then removes them at teardown. Reads SESSION_COOKIE dynamically from
your auth module so the tests work regardless of what you named it.
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SESSION_SECRET", "test-secret-for-pytest-only")

# Test credentials we'll inject — these don't exist in your real USERS dict,
# so they can't accidentally match a real account.
TEST_ADMIN = {
    "username": "__test_admin__",
    "password": "__test_admin_pwd_xyz_123__",
    "name": "Test Admin",
    "role": "admin",
    "email": "test-admin@example.com",
}
TEST_USER = {
    "username": "__test_user__",
    "password": "__test_user_pwd_xyz_123__",
    "name": "Test User",
    "role": "user",
    "email": "test-user@example.com",
}


@pytest.fixture
def _patched_users():
    """
    Inject the two test users into auth.USERS. Tolerant of two USERS schemas:
      A) {"username": {"password": "...", "role": "...", ...}}   ← dict shape
      B) {"username": "password-string"}                         ← bare strings
    """
    import auth

    original = dict(auth.USERS)

    sample = next(iter(auth.USERS.values()), None)
    if isinstance(sample, dict) or sample is None:
        auth.USERS[TEST_ADMIN["username"]] = {
            k: v for k, v in TEST_ADMIN.items() if k != "username"
        }
        auth.USERS[TEST_USER["username"]] = {
            k: v for k, v in TEST_USER.items() if k != "username"
        }
    else:
        auth.USERS[TEST_ADMIN["username"]] = TEST_ADMIN["password"]
        auth.USERS[TEST_USER["username"]] = TEST_USER["password"]

    yield auth

    auth.USERS.clear()
    auth.USERS.update(original)


@pytest.fixture
def session_cookie_name(_patched_users):
    return _patched_users.SESSION_COOKIE


@pytest.fixture
def app(monkeypatch, _patched_users):
    """Fresh FastAPI app per test, with heavy deps stubbed to no-ops."""
    try:
        import db.sqlite_ops as sqlite_ops
        monkeypatch.setattr(sqlite_ops, "get_articles_by_cluster", lambda cid: [], raising=False)
        monkeypatch.setattr(sqlite_ops, "list_topic_queue", lambda limit=100: [], raising=False)
        monkeypatch.setattr(sqlite_ops, "enqueue_topic", lambda t, u: None, raising=False)
        monkeypatch.setattr(sqlite_ops, "update_article", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(sqlite_ops, "add_article_history", lambda *a, **kw: None, raising=False)
    except ImportError:
        pass

    try:
        import db.artifacts as artifacts
        monkeypatch.setattr(artifacts, "list_pipeline_runs", lambda limit=20: [], raising=False)
        monkeypatch.setattr(artifacts, "get_pipeline_run", lambda rid: None, raising=False)
        monkeypatch.setattr(artifacts, "list_artifacts", lambda rid: [], raising=False)
        monkeypatch.setattr(artifacts, "update_pipeline_run", lambda *a, **kw: None, raising=False)
    except ImportError:
        pass

    try:
        import link_engine_integration as lei
        snap = lei.InterlinkSnapshot(
            pending=[], approved=[], injected=[], articles=[], errors=[],
            counts={"pending": 0, "approved": 0, "injected": 0, "articles": 0, "errors": 0},
            available=True,
        )
        monkeypatch.setattr(lei, "get_snapshot", lambda **kw: snap, raising=False)
        monkeypatch.setattr(lei, "approve_anchor", lambda aid: True, raising=False)
        monkeypatch.setattr(lei, "reject_anchor", lambda aid: True, raising=False)
        monkeypatch.setattr(lei, "edit_anchor_text", lambda aid, txt: True, raising=False)
        monkeypatch.setattr(lei, "inject_all_approved",
                            lambda dry_run=False: {"injected": 0, "errors": 0, "skipped": 0},
                            raising=False)
    except ImportError:
        pass

    try:
        from jobs import job_manager
        monkeypatch.setattr(job_manager, "submit", lambda jid, fn, *args: True)
        monkeypatch.setattr(job_manager, "is_active", lambda jid: False)
        monkeypatch.setattr(job_manager, "get_error", lambda jid: None)
    except ImportError:
        pass

    try:
        import scheduler
        monkeypatch.setattr(scheduler, "start_scheduler", lambda: None, raising=False)
    except ImportError:
        pass

    if "app" in sys.modules:
        del sys.modules["app"]
    from app import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def admin_client(app, _patched_users):
    """
    Pre-authenticated client signed in as the injected test admin.
    Verifies the cookie was actually set — fails loudly if login silently
    redirected to /login?error=invalid (means injection didn't work).
    """
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={
        "username": TEST_ADMIN["username"],
        "password": TEST_ADMIN["password"],
    })
    assert r.status_code == 303, f"login HTTP code wrong: {r.status_code}"
    location = r.headers.get("location", "")
    assert "error" not in location.lower(), (
        f"login silently failed; redirected to {location}. "
        f"Test user injection probably didn't work — check your auth.USERS schema."
    )
    return c


@pytest.fixture
def user_client(app, _patched_users):
    """Pre-authenticated client signed in as the injected test user."""
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={
        "username": TEST_USER["username"],
        "password": TEST_USER["password"],
    })
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error" not in location.lower()
    return c