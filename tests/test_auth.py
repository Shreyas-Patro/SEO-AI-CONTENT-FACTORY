"""
tests/test_auth.py
-----------------------------------------------------------------------------
Auth tests. Uses TEST_ADMIN / TEST_USER injected by conftest, and reads
SESSION_COOKIE dynamically from your auth module.
"""
from conftest import TEST_ADMIN, TEST_USER


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_login_with_valid_credentials_sets_cookie(client, session_cookie_name):
    r = client.post("/login", data={
        "username": TEST_ADMIN["username"],
        "password": TEST_ADMIN["password"],
    })
    assert r.status_code == 303
    location = r.headers.get("location", "")
    assert "error" not in location, (
        f"login redirected to error page: {location}. "
        f"User injection in conftest may have failed."
    )
    # Verify the actual cookie name (whatever it is) was set
    set_cookie = r.headers.get_list("set-cookie")
    assert any(session_cookie_name in c for c in set_cookie), (
        f"Set-Cookie didn't include {session_cookie_name}: {set_cookie}"
    )


def test_login_with_bad_credentials_redirects_back(client):
    r = client.post("/login", data={
        "username": TEST_ADMIN["username"],
        "password": "definitely-the-wrong-password",
    })
    assert r.status_code == 303
    assert "/login" in r.headers["location"]
    assert "error" in r.headers["location"]


def test_anonymous_dashboard_blocked_or_redirected(client):
    """Anonymous / should either redirect to landing/login or return 401."""
    r = client.get("/")
    assert r.status_code in (303, 307, 401)


def test_admin_route_requires_admin_role(user_client):
    """A signed-in non-admin user gets 403 from /admin."""
    r = user_client.get("/admin")
    assert r.status_code == 403


def test_admin_route_works_for_admin(admin_client):
    r = admin_client.get("/admin")
    assert r.status_code == 200


def test_logout_clears_cookie(admin_client, session_cookie_name):
    r = admin_client.post("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    set_cookie = r.headers.get("set-cookie", "")
    assert session_cookie_name in set_cookie


def test_signed_session_cookie_round_trip(admin_client):
    """After login, subsequent requests should be authenticated."""
    r = admin_client.get("/admin")
    assert r.status_code == 200


def test_public_pages_are_accessible_anonymously(client):
    """Landing/about/how-to-use should be accessible without auth.
    
    Note: only tests pages where you've applied the new public routes.
    If you skipped Phase 4, these will 404 — that's expected, not a bug.
    """
    for path in ("/login",):                # /login always works
        r = client.get(path)
        assert r.status_code == 200, f"{path} returned {r.status_code}"

    # These only exist if you applied app.py Patch 2 from 00-app-py-patches.md
    for path in ("/landing", "/about", "/how-to-use"):
        r = client.get(path)
        # Either 200 (route exists) or 404 (you haven't added it yet) — both fine
        assert r.status_code in (200, 404), f"{path} returned {r.status_code}"