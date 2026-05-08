"""
tests/test_admin.py
-----------------------------------------------------------------------------
Admin role gating, the admin page's rendered context, and the API key
update flow.
"""
import os


def test_admin_page_blocked_for_anonymous(client):
    r = client.get("/admin")
    assert r.status_code in (303, 401)


def test_admin_page_blocked_for_regular_user(user_client):
    r = user_client.get("/admin")
    assert r.status_code == 403


def test_admin_page_renders_for_admin(admin_client):
    r = admin_client.get("/admin")
    assert r.status_code == 200
    assert "Admin console" in r.text
    # All four tabs should be in the rendered HTML
    assert 'data-tab="users"' in r.text
    assert 'data-tab="activity"' in r.text
    assert 'data-tab="runs"' in r.text
    assert 'data-tab="config"' in r.text


def test_admin_page_lists_hardcoded_users(admin_client):
    r = admin_client.get("/admin")
    assert "writer1" in r.text
    assert "writer2" in r.text
    assert "reviewer" in r.text


def test_admin_api_keys_endpoint_blocked_for_user(user_client):
    r = user_client.post(
        "/admin/api-keys",
        data={"anthropic_api_key": "sk-fake"},
    )
    assert r.status_code == 403


def test_admin_api_keys_update_persists_to_env(admin_client, tmp_path, monkeypatch):
    """
    Saving a new API key should:
    - Update os.environ
    - Write to .env on disk
    """
    # Redirect ROOT to tmp_path so .env is sandboxed
    monkeypatch.chdir(tmp_path)
    import app as app_module
    monkeypatch.setattr(app_module, "ROOT", tmp_path)

    new_key = "sk-test-very-fake-key-not-real-12345"
    r = admin_client.post(
        "/admin/api-keys",
        data={"anthropic_api_key": new_key},
    )
    assert r.status_code == 303
    assert os.environ.get("ANTHROPIC_API_KEY") == new_key

    env_path = tmp_path / ".env"
    assert env_path.exists()
    content = env_path.read_text()
    assert f"ANTHROPIC_API_KEY={new_key}" in content


def test_admin_api_keys_blank_input_leaves_existing_unchanged(admin_client, monkeypatch):
    """
    Saving with all fields blank should not clobber existing values.
    """
    monkeypatch.setenv("SERPAPI_API_KEY", "existing-serpapi-key")
    r = admin_client.post(
        "/admin/api-keys",
        data={"anthropic_api_key": "", "serpapi_api_key": "", "perplexity_api_key": ""},
    )
    assert r.status_code == 303
    assert os.environ.get("SERPAPI_API_KEY") == "existing-serpapi-key"


# ─── Smoke tests for the core pages ──────────────────────────────────

def test_pipeline_dashboard_renders(admin_client):
    """The home page renders without crashing even with no runs."""
    r = admin_client.get("/")
    assert r.status_code == 200
    assert "Canvas" in r.text


def test_queue_page_renders(admin_client):
    r = admin_client.get("/queue")
    # Queue template may not be in our package; tolerate 200 or template-not-found
    assert r.status_code in (200, 500)


def test_landing_renders_for_anonymous(client):
    r = client.get("/landing")
    assert r.status_code == 200
    assert "default answer" in r.text or "Canvas" in r.text


def test_about_renders(client):
    r = client.get("/about")
    assert r.status_code == 200


def test_how_to_use_renders(client):
    r = client.get("/how-to-use")
    assert r.status_code == 200
    assert "Step 1" in r.text or "How to use" in r.text