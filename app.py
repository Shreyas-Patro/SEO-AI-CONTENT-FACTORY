"""
Canvas Homes Dashboard — FastAPI + HTMX.

Run with:
    uvicorn app:app --host 127.0.0.1 --port 8000
"""
import json
import os
import sys
import asyncio
import traceback as _tb_mod
from pathlib import Path
from typing import Optional
from fastapi import Depends
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from auth import (
    login, make_session_cookie, current_user, require_user, require_admin,
    SESSION_COOKIE, USERS,
 )
import asyncio
from fastapi.responses import StreamingResponse
ROOT = Path(__file__).parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from db.artifacts import (
    list_pipeline_runs, get_pipeline_run, list_artifacts,
    update_pipeline_run, load_state,
)
from db.sqlite_ops import add_article_history, get_articles_by_cluster, update_article, db_conn
from orchestrator import (
    start_pipeline_run, run_layer1, run_layer2, run_layer3,
    approve_gate, reject_gate, rerun_agent,
    load_agent_output, load_agent_input, load_agent_metadata,
    load_agent_console, edit_agent_output,
)

from jobs import job_manager

# ─── App setup ────────────────────────────────────────────────────────────

app = FastAPI(title="Canvas Homes Pipeline")

Path("static").mkdir(exist_ok=True)
Path("templates").mkdir(exist_ok=True)
Path("runs").mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.cache = None
templates.env.auto_reload = True


@app.exception_handler(Exception)
async def _all_errors(request: Request, exc: Exception):
    tb = _tb_mod.format_exc()
    print(f"\n[500] {request.url.path}\n{tb}", flush=True)
    return HTMLResponse(
        f"<h1>500 Internal Server Error</h1>"
        f"<pre style='background:#000;color:#0f0;padding:20px;white-space:pre-wrap'>{tb}</pre>",
        status_code=500,
    )


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon():
    return HTMLResponse(content="", status_code=204)


# ─── Constants ────────────────────────────────────────────────────────────

ALL_AGENTS = [
    ("trend_scout",                "📡 Trend Scout",      1),
    ("competitor_spy",             "🕵️ Comp Spy",         1),
    ("keyword_mapper",             "🗺️ KW Mapper",        1),
    ("content_architect",          "🏗️ Architect",        2),
    ("faq_architect",              "❓ FAQs",             2),
    ("research_prompt_generator",  "🔬 Research",         2),
    ("lead_writer",                "✍️ Writer",           3),
    ("fact_verifier",              "🔍 Fact Verify",      3),
    ("brand_auditor",              "🎨 Brand Audit",      3),
    ("rewriter",                   "🔄 Rewriter",         3),
    ("meta_tagger",                "🏷️ Meta Tagger",      3),
]


# ─── Helpers ──────────────────────────────────────────────────────────────

def _agent_status(run_id: str, agent_name: str) -> str:
    base = ROOT / "runs" / run_id / agent_name
    if not base.exists():
        return "pending"
    out = base / "output.json"
    meta_f = base / "metadata.json"
    if out.exists():
        if meta_f.exists():
            try:
                d = json.loads(meta_f.read_text(encoding="utf-8"))
                if d.get("status") == "failed":
                    return "failed"
            except Exception:
                pass
        return "done"
    if (base / "input.json").exists():
        return "active"
    return "pending"


def _agent_meta_summary(run_id: str, agent_name: str) -> dict:
    meta = load_agent_metadata(run_id, agent_name) or {}
    return {
        "cost_usd": meta.get("cost_usd", 0) or 0,
        "duration_seconds": meta.get("duration_seconds", 0) or 0,
        "llm_calls": meta.get("llm_calls", 0) or 0,
        "serp_calls": meta.get("serp_calls", 0) or 0,
        "validation_problems": meta.get("validation_problems") or [],
        "attempts": meta.get("attempts") or 1,
    }


def _agents_grouped(run_id: str) -> list:
    layer_labels = {
        1: "Layer 1 — Discovery",
        2: "Layer 2 — Architecture",
        3: "Layer 3 — Writing & Quality",
    }
    by_layer = {1: [], 2: [], 3: []}
    for key, label, layer in ALL_AGENTS:
        status = _agent_status(run_id, key)
        meta = _agent_meta_summary(run_id, key) if status in ("done", "failed") else {}
        by_layer[layer].append({
            "key": key,
            "label": label,
            "status": status,
            "meta": meta,
        })
    return [
        {"num": n, "label": layer_labels[n], "agents": by_layer[n]}
        for n in (1, 2, 3)
    ]

def _job_state_for_run(run_id: str) -> dict:
    return {
        "l1": job_manager.is_active(f"{run_id}:l1"),
        "l2": job_manager.is_active(f"{run_id}:l2"),
        "l3": job_manager.is_active(f"{run_id}:l3"),
        "errors": {
            "l1": job_manager.get_error(f"{run_id}:l1"),
            "l2": job_manager.get_error(f"{run_id}:l2"),
            "l3": job_manager.get_error(f"{run_id}:l3"),
        },
    }

def _layer3_summary(run_id: str) -> dict:
    """Per-cluster Layer 3 stats: how many articles done, in flight, queued."""
    run = get_pipeline_run(run_id)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        return {"total": 0, "written": 0, "drafts": 0, "needs_review": 0}
    arts = get_articles_by_cluster(cluster_id)
    return {
        "total":         len(arts),
        "written":       sum(1 for a in arts if a.get("status") == "written"),
        "drafts":        sum(1 for a in arts if a.get("status") == "draft"),
        "needs_review":  sum(1 for a in arts if a.get("status") == "needs_human_review"),
        "current_stages": {
            a["title"]: a.get("current_stage", "?")
            for a in arts if a.get("current_stage") not in ("planned", "meta_tagger")
        },
    }
# ─── Pages ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, run_id: Optional[str] = None):
    runs = list_pipeline_runs(limit=20)
    if run_id is None and runs:
        run_id = runs[0]["id"]
    run = get_pipeline_run(run_id) if run_id else None
    return templates.TemplateResponse(request, "index.html", {
        "runs": runs,
        "run": run,
        "run_id": run_id,
        "agents": _agents_grouped(run_id) if run_id else [],
        "job_state": _job_state_for_run(run_id) if run_id else {
            "l1": False, "l2": False, "l3": False, "errors": {}
        },
    })


# ─── Run management ───────────────────────────────────────────────────────

@app.post("/runs/new")
async def create_run(topic: str = Form(...)):
    run_id = start_pipeline_run(topic)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/reset_l3")
async def reset_l3(run_id: str):
    update_pipeline_run(run_id, status="running", current_stage="layer2_done")
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/gate/approve")
async def gate_approve(run_id: str):
    approve_gate(run_id)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/gate/reject")
async def gate_reject(run_id: str):
    reject_gate(run_id)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


# ─── Layer execution ──────────────────────────────────────────────────────

@app.post("/runs/{run_id}/layer/{layer}")
async def run_layer(run_id: str, layer: str):
    if layer not in ("l1", "l2", "l3"):
        raise HTTPException(400, "Bad layer")
    fn_map = {"l1": run_layer1, "l2": run_layer2, "l3": run_layer3}
    if layer == "l3":
        run = get_pipeline_run(run_id)
        if run and run.get("status") == "completed":
            update_pipeline_run(run_id, status="running", current_stage="layer2_done")
    job_manager.submit(f"{run_id}:{layer}", fn_map[layer], run_id)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


# ─── HTMX partials ────────────────────────────────────────────────────────

@app.get("/runs/{run_id}/agents", response_class=HTMLResponse)
async def agents_partial(request: Request, run_id: str):
    return templates.TemplateResponse(request, "_agents_grid.html", {
        "agents": _agents_grouped(run_id),
        "run_id": run_id,
    })


@app.get("/runs/{run_id}/header", response_class=HTMLResponse)
async def header_partial(request: Request, run_id: str):
    run = get_pipeline_run(run_id)
    return templates.TemplateResponse(request, "_header.html", {
        "run": run,
        "run_id": run_id,
        "job_state": _job_state_for_run(run_id),
    })


@app.get("/runs/{run_id}/log", response_class=HTMLResponse)
async def log_partial(request: Request, run_id: str):
    log_file = ROOT / "runs" / run_id / "_live.log"
    text = ""
    if log_file.exists():
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
    text = text[-8000:] if len(text) > 8000 else text
    return templates.TemplateResponse(request, "_live_log.html", {
        "log_text": text or "(no output yet)",
    })


@app.get("/runs/{run_id}/agent/{agent}/detail", response_class=HTMLResponse)
async def agent_detail(request: Request, run_id: str, agent: str):
    inp = load_agent_input(run_id, agent)
    out = load_agent_output(run_id, agent)
    meta = load_agent_metadata(run_id, agent)
    console = load_agent_console(run_id, agent) or ""
    return templates.TemplateResponse(request, "_agent_detail.html", {
        "run_id": run_id,
        "agent": agent,
        "input": json.dumps(inp, indent=2, default=str) if inp else None,
        "output": json.dumps(out, indent=2, default=str) if out else None,
        "meta": meta or {},
        "console": console[-5000:] if console else "",
    })


@app.post("/runs/{run_id}/agent/{agent}/rerun")
async def agent_rerun_route(run_id: str, agent: str):
    if agent == "trend_scout":
        raise HTTPException(400, "trend_scout cannot be rerun standalone")
    job_manager.submit(f"{run_id}:rerun:{agent}", rerun_agent, run_id, agent)
    return JSONResponse({"ok": True})


@app.post("/runs/{run_id}/agent/{agent}/save")
async def agent_save(run_id: str, agent: str, output_json: str = Form(...)):
    try:
        parsed = json.loads(output_json)
        edit_agent_output(run_id, agent, parsed)
        return JSONResponse({"ok": True})
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ─── Articles ─────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}/articles", response_class=HTMLResponse)
async def articles_partial(request: Request, run_id: str):
    run = get_pipeline_run(run_id)
    cluster_id = run.get("cluster_id") if run else None
    articles = []
    if cluster_id:
        articles = get_articles_by_cluster(cluster_id)
        for a in articles:
            try:
                a["faqs_parsed"] = json.loads(a.get("faq_json", "[]") or "[]")
            except Exception:
                a["faqs_parsed"] = []
    return templates.TemplateResponse(request, "_articles.html", {
        "run_id": run_id,
        "articles": articles,
    })


@app.post("/articles/{article_id}/delete")
async def article_delete(article_id: str):
    with db_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        conn.commit()
    return JSONResponse({"ok": True})


@app.post("/articles/{article_id}/save")
async def article_save(article_id: str, content_md: str = Form(...)):
    wc = len(content_md.split())
    update_article(article_id, content_md=content_md, word_count=wc)
    return JSONResponse({"ok": True, "word_count": wc})


@app.post("/articles/{article_id}/faq/{idx}/delete")
async def faq_delete(article_id: str, idx: int):
    with db_conn() as conn:
        row = conn.execute("SELECT faq_json FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not row:
            return JSONResponse({"ok": False}, status_code=404)
        try:
            faqs = json.loads(row[0] or "[]")
        except Exception:
            faqs = []
        if 0 <= idx < len(faqs):
            faqs.pop(idx)
            conn.execute("UPDATE articles SET faq_json = ? WHERE id = ?", (json.dumps(faqs), article_id))
            conn.commit()
    return JSONResponse({"ok": True})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = login(username, password)
    if not user:
        return RedirectResponse("/login?error=invalid", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=make_session_cookie(user),
        httponly=True,
        max_age=60 * 60 * 24 * 14,  # 14 days
        samesite="lax",
    )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
# ─── Sidebar runs list ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, run_id: Optional[str] = None,
               user: dict = Depends(require_user)):
    # ... existing body, but pass user into the template:
    runs = list_pipeline_runs(limit=20)
    if run_id is None and runs:
        run_id = runs[0]["id"]
    run = get_pipeline_run(run_id) if run_id else None
    return templates.TemplateResponse(request, "index.html", {
        "runs": runs,
        "run": run,
        "run_id": run_id,
        "agents": _agents_grouped(run_id) if run_id else [],
        "job_state": _job_state_for_run(run_id) if run_id else {
            "l1": False, "l2": False, "l3": False, "errors": {}
        },
        "user": user,                        # NEW
        "is_admin": user["role"] == "admin", # NEW
    })

@app.get("/runs/{run_id}/log/stream")
async def log_stream(run_id: str, request: Request,
                     user: dict = Depends(require_user)):
    """Server-Sent Events stream of the live log file."""
    log_file = ROOT / "runs" / run_id / "_live.log"

    async def event_gen():
        last_size = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                if log_file.exists():
                    size = log_file.stat().st_size
                    if size > last_size:
                        with log_file.open("r", encoding="utf-8", errors="replace") as f:
                            f.seek(last_size)
                            new = f.read()
                        last_size = size
                        # SSE format: each line prefixed with "data: ", terminated by \n\n
                        for line in new.splitlines():
                            yield f"data: {line}\n\n"
            except Exception as e:
                yield f"data: [stream error] {e}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
@app.get("/runs/{run_id}/articles/{article_id}", response_class=HTMLResponse)
async def article_inspector(request: Request, run_id: str, article_id: str,
                            user: dict = Depends(require_user)):
    from db.sqlite_ops import get_article
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    return templates.TemplateResponse(request, "_article_inspector.html", {
        "run_id": run_id,
        "article": article,
    })
@app.post("/runs/{run_id}/interlink/cluster")
async def run_cluster_interlink(
    run_id: str,
    user: dict = Depends(require_user),
):
    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "No cluster_id")
    job_manager.submit(
        f"{run_id}:interlink_cluster",
        _run_cluster_pass,
        run["cluster_id"], run_id,
    )
    return RedirectResponse(f"/runs/{run_id}/interlink", status_code=303)


def _run_cluster_pass(cluster_id: str, run_id: str):
    """Wraps link_engine_bridge.cluster_pass with logging."""
    from link_engine_bridge import cluster_pass
    print(f"[interlink] starting cluster pass for {cluster_id}")
    result = cluster_pass(cluster_id, run_id)
    print(f"[interlink] cluster pass done: {len(result.get('report', []))} candidates")


@app.get("/runs/{run_id}/interlink", response_class=HTMLResponse)
async def interlink_view(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    run = get_pipeline_run(run_id)
    cluster_id = run.get("cluster_id") if run else None

    # Read the latest link_report.json if it exists
    report_path = ROOT / "output" / "link_report.json"
    candidates = []
    if report_path.exists():
        try:
            candidates = json.loads(report_path.read_text())
        except Exception:
            pass

    # Also pull pending anchors from link_engine.db so users can approve
    pending = []
    try:
        from link_engine.db.session import get_session_factory
        from link_engine.db.models import Anchor, Match
        session = get_session_factory()()
        try:
            pending_anchors = (
                session.query(Anchor)
                .filter(Anchor.status == "pending_review")
                .join(Anchor.match)
                .order_by(Match.similarity_score.desc())
                .limit(100)
                .all()
            )
            for a in pending_anchors:
                m = a.match
                pending.append({
                    "anchor_id": a.anchor_id,
                    "anchor_text": a.edited_anchor or a.anchor_text or m.matched_phrase,
                    "source_title": m.source_chunk.article.title,
                    "target_title": m.target_chunk.article.title,
                    "similarity": round(m.similarity_score, 3),
                    "confidence": a.llm_confidence or 0,
                    "reasoning": a.reasoning or "",
                })
        finally:
            session.close()
    except Exception as e:
        pending = [{"error": str(e)}]

    return templates.TemplateResponse(request, "_interlink.html", {
        "run_id": run_id,
        "cluster_id": cluster_id,
        "candidates": candidates,
        "pending": pending,
        "interlink_running": job_manager.is_active(f"{run_id}:interlink_cluster")
                             or job_manager.is_active(f"{run_id}:interlink_global"),
    })


@app.post("/interlink/anchor/{anchor_id}/approve")
async def interlink_approve(anchor_id: str, user: dict = Depends(require_user)):
    from link_engine.db.session import get_session_factory
    from link_engine.db.models import Anchor
    session = get_session_factory()()
    try:
        a = session.get(Anchor, anchor_id)
        if a:
            a.status = "approved"
            session.commit()
    finally:
        session.close()
    return JSONResponse({"ok": True})


@app.post("/interlink/anchor/{anchor_id}/reject")
async def interlink_reject(anchor_id: str, user: dict = Depends(require_user)):
    from link_engine.db.session import get_session_factory
    from link_engine.db.models import Anchor
    session = get_session_factory()()
    try:
        a = session.get(Anchor, anchor_id)
        if a:
            a.status = "rejected"
            session.commit()
    finally:
        session.close()
    return JSONResponse({"ok": True})


@app.post("/runs/{run_id}/interlink/inject")
async def interlink_inject(
    run_id: str,
    dry_run: bool = Form(False),
    user: dict = Depends(require_user),
):
    from link_engine.db.session import get_session_factory
    from link_engine.db.models import Anchor, Run, Injection
    from link_engine.stages.inject import inject_approved_links
    session = get_session_factory()()
    try:
        approved = (
            session.query(Anchor)
            .filter(Anchor.status == "approved")
            .filter(~Anchor.anchor_id.in_(session.query(Injection.anchor_id)))
            .all()
        )
        run = Run(articles_processed=0)
        session.add(run)
        session.flush()
        results = inject_approved_links(approved, session, run.run_id, dry_run=dry_run)
        session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/runs/{run_id}/interlink?injected={results['injected']}",
                            status_code=303)

@app.post("/articles/{article_id}/edit")
async def article_edit(
    article_id: str,
    content_md: str = Form(...),
    user: dict = Depends(require_user),
):
    wc = len(content_md.split())
    update_article(article_id, content_md=content_md, word_count=wc, status="edited")
    add_article_history(
        article_id, "human_edit",
        f"Edited by {user['username']} ({wc} words)",
        content_md[:500],
    )
    return JSONResponse({"ok": True, "word_count": wc})
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)