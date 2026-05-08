"""
tests/test_interlink.py
-----------------------------------------------------------------------------
Tests for the migrated interlink dashboard and its action endpoints.
The link_engine_integration module is stubbed in conftest, so these tests
exercise routing + auth + template rendering, not the SQL layer.
"""


def test_interlink_view_requires_auth(client):
    r = client.get("/runs/test-run/interlink")
    # Either 303 redirect to login or 401
    assert r.status_code in (303, 401)


def test_interlink_view_renders_for_admin(admin_client, monkeypatch):
    # Make get_pipeline_run return something with a cluster_id
    import db.artifacts as artifacts
    monkeypatch.setattr(
        artifacts, "get_pipeline_run",
        lambda rid: {"id": rid, "topic": "test", "cluster_id": "cluster-1"},
    )
    r = admin_client.get("/runs/test-run/interlink")
    assert r.status_code == 200
    assert "Interlink dashboard" in r.text


def test_approve_endpoint_returns_ok(admin_client):
    r = admin_client.post("/interlink/anchor/abc-123/approve")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_reject_endpoint_returns_ok(admin_client):
    r = admin_client.post("/interlink/anchor/abc-123/reject")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_edit_endpoint_accepts_new_text(admin_client):
    r = admin_client.post(
        "/interlink/anchor/abc-123/edit",
        data={"anchor_text": "new anchor text"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_bulk_approve_returns_count(admin_client, monkeypatch):
    """With three pending anchors, bulk-approve should report 3 approved."""
    import link_engine_integration as lei
    snap = lei.InterlinkSnapshot(
        pending=[
            {"anchor_id": "a1", "anchor_text": "x", "source_title": "s",
             "target_title": "t", "similarity": 0.9, "confidence": 4,
             "reasoning": "", "score_band": "high",
             "source_text": "", "target_text": "",
             "source_slug": "", "target_slug": ""},
            {"anchor_id": "a2", "anchor_text": "y", "source_title": "s",
             "target_title": "t", "similarity": 0.8, "confidence": 4,
             "reasoning": "", "score_band": "high",
             "source_text": "", "target_text": "",
             "source_slug": "", "target_slug": ""},
            {"anchor_id": "a3", "anchor_text": "z", "source_title": "s",
             "target_title": "t", "similarity": 0.7, "confidence": 4,
             "reasoning": "", "score_band": "mid",
             "source_text": "", "target_text": "",
             "source_slug": "", "target_slug": ""},
        ],
        approved=[], injected=[], articles=[], errors=[],
        counts={"pending": 3, "approved": 0, "injected": 0, "articles": 0, "errors": 0},
        available=True,
    )
    monkeypatch.setattr(lei, "get_snapshot", lambda **kw: snap)
    monkeypatch.setattr(lei, "approve_anchor", lambda aid: True)

    r = admin_client.post("/interlink/bulk/approve_all")
    assert r.status_code == 200
    assert r.json()["approved"] == 3


def test_inject_calls_wrapper(admin_client, monkeypatch):
    """The inject endpoint should redirect to the interlink page."""
    r = admin_client.post(
        "/runs/test-run/interlink/inject",
        data={"dry_run": "false"},
    )
    assert r.status_code == 303
    assert "/interlink" in r.headers["location"]