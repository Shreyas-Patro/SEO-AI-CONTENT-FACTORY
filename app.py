"""
Canvas Homes Dashboard — FastAPI + HTMX.

Run with:
    uvicorn app:app --host 127.0.0.1 --port 8000
"""
import asyncio
import json
import os
import sys
import io
import zipfile
import re
from auth import optional_user
import traceback as _tb_mod
from pathlib import Path
from typing import Optional
from export_for_link_engine import export_cluster_to_zip, article_to_link_engine_md
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
# Add near the other local imports
from link_engine_integration import (
    get_snapshot as interlink_snapshot,
    approve_anchor,
    reject_anchor,
    edit_anchor_text,
    inject_all_approved,
)

ROOT = Path(__file__).parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# Local imports (after sys.path tweak so they resolve when uvicorn is run from anywhere)
from auth import (
    SESSION_COOKIE,
    USERS,
    current_user,
    login,
    make_session_cookie,
    require_admin,
    require_user,
)
from db.artifacts import (
    get_pipeline_run,
    list_artifacts,
    list_pipeline_runs,
    load_state,
    update_pipeline_run,
)
from db.sqlite_ops import (
    add_article_history,
    db_conn,
    enqueue_topic,
    get_articles_by_cluster,
    list_topic_queue,
    update_article,
)
from jobs import job_manager
from orchestrator import (
    approve_gate,
    edit_agent_output,
    load_agent_console,
    load_agent_input,
    load_agent_metadata,
    load_agent_output,
    reject_gate,
    rerun_agent,
    run_layer1,
    run_layer2,
    run_layer3,
    start_pipeline_run,
)
from scheduler import start_scheduler


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

class AuthRedirectMiddleware(BaseHTTPMiddleware):
    """
    If a route raises HTTPException(401), redirect HTML clients to /login.
    JSON clients (HTMX/fetch) still get the 401 so client-side code can
    detect the auth failure cleanly.
    """
    async def dispatch(self, request: StarletteRequest, call_next):
        response: StarletteResponse = await call_next(request)
        if response.status_code == 401:
            accepts = request.headers.get("accept", "")
            wants_html = "text/html" in accepts and "application/json" not in accepts
            if wants_html and not request.url.path.startswith("/login"):
                redirect = RedirectResponse(
                    f"/login?next={request.url.path}",
                    status_code=303,
                )
                return redirect
        return response


app.add_middleware(AuthRedirectMiddleware)

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

def _ctx(user: dict, **kwargs) -> dict:
    """Base template context — every template gets user and is_admin."""
    return {
        "user": user,
        "is_admin": (user or {}).get("role") == "admin",
        **kwargs,
    }


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
        return {"total": 0, "written": 0, "drafts": 0, "needs_review": 0,
                "current_stages": {}}
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


# ─── Auth pages (PUBLIC) ──────────────────────────────────────────────────

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


def _slugify(text: str) -> str:
    """Fallback slug if meta_tagger didn't produce one."""
    if not text:
        return "untitled"
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:80] or "untitled"


def _article_to_markdown(article: dict) -> tuple[str, str]:
    """Returns (filename, markdown_content) using your real DB schema."""
    import json as _json

    title = article.get("title") or "Untitled"
    slug = article.get("slug") or _slugify(title)
    body = article.get("content_md") or ""

    # Parse schema_json for the SEO blob
    try:
        schema = _json.loads(article.get("schema_json") or "{}")
    except Exception:
        schema = {}

    # Parse target_keywords
    try:
        target_kw = _json.loads(article.get("target_keywords") or "{}")
    except Exception:
        target_kw = {}

    # Build frontmatter using YOUR actual columns
    front = ["---"]
    front.append(f'title: "{(article.get("meta_title") or title).replace(chr(34), chr(39))}"')
    front.append(f'slug: "{slug}"')

    desc = article.get("meta_description") or schema.get("meta_description") or ""
    if desc:
        front.append(f'description: "{desc.replace(chr(34), chr(39))[:300]}"')

    canonical = schema.get("canonical_url") or ""
    if canonical:
        front.append(f'canonical_url: "{canonical}"')

    primary_kw = target_kw.get("primary") or schema.get("focus_keyword") or ""
    if primary_kw:
        front.append(f'primary_keyword: "{primary_kw}"')

    secondary = target_kw.get("secondary") or []
    if secondary:
        sec_str = ", ".join(f'"{k}"' for k in secondary[:8])
        front.append(f'secondary_keywords: [{sec_str}]')

    if article.get("article_type"):
        front.append(f'type: "{article["article_type"]}"')

    front.append(f'word_count: {article.get("word_count") or 0}')
    if article.get("brand_tone_score") is not None:
        front.append(f'brand_score: {article["brand_tone_score"]}')
    if article.get("fact_check_score") is not None:
        front.append(f'fact_score: {article["fact_check_score"]}')
    if article.get("readability_score") is not None:
        front.append(f'readability_score: {article["readability_score"]}')

    # OG tags from schema
    if schema.get("og_title"):
        front.append(f'og_title: "{schema["og_title"].replace(chr(34), chr(39))}"')
    if schema.get("og_description"):
        front.append(f'og_description: "{schema["og_description"].replace(chr(34), chr(39))[:300]}"')

    # Tags + category
    if schema.get("tags"):
        tag_str = ", ".join(f'"{t}"' for t in schema["tags"][:10])
        front.append(f'tags: [{tag_str}]')
    if schema.get("category"):
        front.append(f'category: "{schema["category"]}"')

    front.append("---")
    front.append("")

    md = "\n".join(front) + body.rstrip()

    # Append FAQ section
    try:
        faqs = _json.loads(article.get("faq_json") or "[]")
    except Exception:
        faqs = []

    if faqs and "## Frequently Asked Questions" not in body:
        md += "\n\n## Frequently Asked Questions\n\n"
        for f in faqs:
            q = (f.get("question") or "").strip()
            a_text = (f.get("answer") or "").strip()
            if q and a_text:
                md += f"### {q}\n\n{a_text}\n\n"

    # Append JSON-LD schema as HTML comment so Hugo/Astro can pick it up
    if schema.get("schema_article") or schema.get("schema_faq") or schema.get("schema_breadcrumb"):
        md += "\n\n<!-- JSON-LD SCHEMA -->\n"
        for key in ("schema_article", "schema_faq", "schema_breadcrumb"):
            if schema.get(key):
                md += f'<script type="application/ld+json">\n'
                md += _json.dumps(schema[key], indent=2)
                md += "\n</script>\n\n"

    filename = f"{slug}.md"
    return filename, md

@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request, user: dict = Depends(optional_user)):
    return templates.TemplateResponse(request, "landing.html", {
        "user": user,
        "is_admin": (user or {}).get("role") == "admin",
        "active_page": "landing",
    })

@app.get("/about", response_class=HTMLResponse)
async def about(request: Request, user: dict = Depends(optional_user)):
    return templates.TemplateResponse(request, "about.html", {
        "user": user,
        "is_admin": (user or {}).get("role") == "admin",
        "active_page": "about",
    })

@app.get("/how-to-use", response_class=HTMLResponse)
async def how_to_use(request: Request, user: dict = Depends(optional_user)):
    return templates.TemplateResponse(request, "how_to_use.html", {
        "user": user,
        "is_admin": (user or {}).get("role") == "admin",
        "active_page": "how",
    })

@app.get("/articles/{article_id}/download")
async def article_download(article_id: str, user: dict = Depends(require_user)):
    """Download a single article as .md with frontmatter."""
    from db.sqlite_ops import get_article
    from fastapi.responses import Response

    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")

    filename, content = _article_to_markdown(article)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/runs/{run_id}/articles/download")
async def run_articles_download(run_id: str, user: dict = Depends(require_user)):
    """Download all articles in a run as a single .zip of .md files."""
    from fastapi.responses import Response

    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "Run has no cluster_id")

    articles = get_articles_by_cluster(run["cluster_id"]) or []
    if not articles:
        raise HTTPException(404, "No articles to download")

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = {}
        for a in articles:
            filename, content = _article_to_markdown(a)
            # Avoid filename collisions
            if filename in seen:
                seen[filename] += 1
                base, ext = filename.rsplit(".", 1)
                filename = f"{base}-{seen[filename]}.{ext}"
            else:
                seen[filename] = 1
            zf.writestr(filename, content)

        # Also include a manifest.json with all metadata
        import json as _json
        manifest = {
            "run_id": run_id,
            "cluster_id": run["cluster_id"],
            "topic": run.get("topic"),
            "article_count": len(articles),
            "exported_at": _now() if "_now" in dir() else None,
            "articles": [],
        }
        for a in articles:
            try:
                tk = json.loads(a.get("target_keywords") or "{}")
            except Exception:
                tk = {}
            try:
                sch = json.loads(a.get("schema_json") or "{}")
            except Exception:
                sch = {}
            manifest["articles"].append({
                "id": a.get("id"),
                "title": a.get("title"),
                "meta_title": a.get("meta_title"),
                "slug": a.get("slug") or _slugify(a.get("title", "")),
                "type": a.get("article_type"),
                "status": a.get("status"),
                "word_count": a.get("word_count"),
                "brand_tone_score": a.get("brand_tone_score"),
                "fact_check_score": a.get("fact_check_score"),
                "readability_score": a.get("readability_score"),
                "primary_keyword": tk.get("primary"),
                "secondary_keywords": tk.get("secondary", []),
                "canonical_url": sch.get("canonical_url"),
                "tags": sch.get("tags", []),
                "category": sch.get("category"),
            })
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

    buf.seek(0)
    zip_filename = f"{run.get('topic', run_id)[:40].replace(' ', '_')}-articles.zip"
    zip_filename = re.sub(r"[^\w\-.]", "", zip_filename) or f"{run_id}-articles.zip"

    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )

@app.get("/runs/{run_id}/articles/download_for_link_engine")
async def download_for_link_engine(
    run_id: str,
    user: dict = Depends(require_user),
):
    from fastapi.responses import Response
    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "Run has no cluster_id")
    cluster_id = run["cluster_id"]
    zip_bytes = export_cluster_to_zip(cluster_id)
    filename = f"{cluster_id}-link-engine-ready.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/articles/{article_id}/download_for_link_engine")
async def download_one_for_link_engine(
    article_id: str,
    user: dict = Depends(require_user),
):
    from fastapi.responses import Response
    from db.sqlite_ops import get_article
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    filename, content = article_to_link_engine_md(article)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─── Home ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    run_id: Optional[str] = None,
    user: Optional[dict] = Depends(optional_user),  # CHANGED from require_user
):
      # Anonymous users go to the marketing page, not the dashboard
    if not user:
        return RedirectResponse("/landing", status_code=303)
    runs = list_pipeline_runs(limit=20)
    if run_id is None and runs:
        run_id = runs[0]["id"]
    run = get_pipeline_run(run_id) if run_id else None
    return templates.TemplateResponse(request, "index.html", _ctx(user,
        runs=runs,
        run=run,
        run_id=run_id,
        agents=_agents_grouped(run_id) if run_id else [],
        layer3_summary=_layer3_summary(run_id) if run_id else None,
        job_state=_job_state_for_run(run_id) if run_id else {
            "l1": False, "l2": False, "l3": False, "errors": {}
        },
    ))


# ─── Run management ───────────────────────────────────────────────────────

@app.post("/runs/new")
async def create_run(
    topic: str = Form(...),
    user: dict = Depends(require_user),
):
    run_id = start_pipeline_run(topic, submitted_by=user["username"])
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/reset_l3")
async def reset_l3(run_id: str, user: dict = Depends(require_user)):
    update_pipeline_run(run_id, status="running", current_stage="layer2_done")
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/gate/approve")
async def gate_approve(run_id: str, user: dict = Depends(require_user)):
    approve_gate(run_id)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/gate/reject")
async def gate_reject(run_id: str, user: dict = Depends(require_user)):
    reject_gate(run_id)
    return RedirectResponse(f"/?run_id={run_id}", status_code=303)


@app.post("/runs/{run_id}/layer/{layer}")
async def run_layer(run_id: str, layer: str, user: dict = Depends(require_user)):
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

@app.get("/runs/list", response_class=HTMLResponse)
async def runs_list_partial(request: Request, user: dict = Depends(require_user)):
    runs = list_pipeline_runs(limit=20)
    return templates.TemplateResponse(request, "_runs_list.html", _ctx(user, runs=runs))


@app.get("/runs/{run_id}/agents", response_class=HTMLResponse)
async def agents_partial(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    return templates.TemplateResponse(request, "_agents_grid.html", _ctx(user,
        agents=_agents_grouped(run_id),
        layer3_summary=_layer3_summary(run_id),
        run_id=run_id,
    ))


@app.get("/runs/{run_id}/header", response_class=HTMLResponse)
async def header_partial(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    run = get_pipeline_run(run_id)
    return templates.TemplateResponse(request, "_header.html", _ctx(user,
        run=run,
        run_id=run_id,
        job_state=_job_state_for_run(run_id),
    ))


@app.get("/runs/{run_id}/log", response_class=HTMLResponse)
async def log_partial(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    log_file = ROOT / "runs" / run_id / "_live.log"
    text = ""
    if log_file.exists():
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
    text = text[-8000:] if len(text) > 8000 else text
    return templates.TemplateResponse(request, "_live_log.html", _ctx(user,
        log_text=text or "(no output yet)",
    ))


@app.get("/runs/{run_id}/log/stream")
async def log_stream(
    run_id: str,
    request: Request,
    user: dict = Depends(require_user),
):
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
                        for line in new.splitlines():
                            yield f"data: {line}\n\n"
            except Exception as e:
                yield f"data: [stream error] {e}\n\n"
            await asyncio.sleep(1.5)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/runs/{run_id}/agent/{agent}/detail", response_class=HTMLResponse)
async def agent_detail(
    request: Request,
    run_id: str,
    agent: str,
    user: dict = Depends(require_user),
):
    inp = load_agent_input(run_id, agent)
    out = load_agent_output(run_id, agent)
    meta = load_agent_metadata(run_id, agent)
    console = load_agent_console(run_id, agent) or ""
    return templates.TemplateResponse(request, "_agent_detail.html", _ctx(user,
        run_id=run_id,
        agent=agent,
        input=json.dumps(inp, indent=2, default=str) if inp else None,
        output=json.dumps(out, indent=2, default=str) if out else None,
        meta=meta or {},
        console=console[-5000:] if console else "",
    ))


@app.post("/runs/{run_id}/agent/{agent}/rerun")
async def agent_rerun_route(
    run_id: str,
    agent: str,
    user: dict = Depends(require_user),
):
    if agent == "trend_scout":
        raise HTTPException(400, "trend_scout cannot be rerun standalone")
    job_manager.submit(f"{run_id}:rerun:{agent}", rerun_agent, run_id, agent)
    return JSONResponse({"ok": True})


@app.post("/runs/{run_id}/agent/{agent}/save")
async def agent_save(
    run_id: str,
    agent: str,
    output_json: str = Form(...),
    user: dict = Depends(require_user),
):
    try:
        parsed = json.loads(output_json)
        edit_agent_output(run_id, agent, parsed)
        return JSONResponse({"ok": True})
    except json.JSONDecodeError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ─── Articles ─────────────────────────────────────────────────────────────

@app.get("/runs/{run_id}/articles", response_class=HTMLResponse)
async def articles_partial(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    run = get_pipeline_run(run_id)
    cluster_id = run.get("cluster_id") if run else None
    articles = []
    if cluster_id:
        articles = get_articles_by_cluster(cluster_id) or []
        for a in articles:
            try:
                a["faqs_parsed"] = json.loads(a.get("faq_json", "[]") or "[]")
            except Exception:
                a["faqs_parsed"] = []
            try:
                a["target_keywords_parsed"] = json.loads(a.get("target_keywords") or "{}")
            except Exception:
                a["target_keywords_parsed"] = {}
            try:
                schema = json.loads(a.get("schema_json") or "{}")
                a["canonical_url"] = schema.get("canonical_url", "")
            except Exception:
                a["canonical_url"] = ""

    return templates.TemplateResponse(request, "_articles.html", _ctx(user,
        run_id=run_id,
        articles=articles,
    ))


@app.get("/runs/{run_id}/articles/{article_id}", response_class=HTMLResponse)
async def article_inspector(
    request: Request,
    run_id: str,
    article_id: str,
    user: dict = Depends(require_user),
):
    from db.sqlite_ops import get_article
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    return templates.TemplateResponse(request, "_article_inspector.html", _ctx(user,
        run_id=run_id,
        article=article,
    ))


@app.post("/articles/{article_id}/delete")
async def article_delete(article_id: str, user: dict = Depends(require_user)):
    with db_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        conn.commit()
    return JSONResponse({"ok": True})


@app.post("/articles/{article_id}/save")
async def article_save(
    article_id: str,
    content_md: str = Form(...),
    user: dict = Depends(require_user),
):
    wc = len(content_md.split())
    update_article(article_id, content_md=content_md, word_count=wc)
    return JSONResponse({"ok": True, "word_count": wc})


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


@app.post("/articles/{article_id}/faq/{idx}/delete")
async def faq_delete(
    article_id: str,
    idx: int,
    user: dict = Depends(require_user),
):
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
            conn.execute(
                "UPDATE articles SET faq_json = ? WHERE id = ?",
                (json.dumps(faqs), article_id),
            )
            conn.commit()
    return JSONResponse({"ok": True})


# ─── Interlinking ─────────────────────────────────────────────────────────

def _run_cluster_pass(cluster_id: str, run_id: str):
    """Wraps link_engine_bridge.cluster_pass with logging."""
    from link_engine_bridge import cluster_pass
    print(f"[interlink] starting cluster pass for {cluster_id}")
    result = cluster_pass(cluster_id, run_id)
    print(f"[interlink] cluster pass done: {len(result.get('report', []))} candidates")


def _run_global_pass(cluster_id: str, run_id: str):
    """Wraps link_engine_bridge.global_pass with logging."""
    from link_engine_bridge import global_pass
    print(f"[interlink] starting global pass for {cluster_id}")
    result = global_pass(cluster_id, run_id)
    print(f"[interlink] global pass done: {len(result.get('report', []))} candidates")


@app.post("/runs/{run_id}/interlink/cluster")
async def run_cluster_interlink(run_id: str, user: dict = Depends(require_user)):
    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "No cluster_id")
    job_manager.submit(
        f"{run_id}:interlink_cluster",
        _run_cluster_pass,
        run["cluster_id"], run_id,
    )
    return RedirectResponse(f"/runs/{run_id}/interlink", status_code=303)


@app.post("/runs/{run_id}/interlink/global")
async def run_global_interlink(run_id: str, user: dict = Depends(require_user)):
    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "No cluster_id")
    job_manager.submit(
        f"{run_id}:interlink_global",
        _run_global_pass,
        run["cluster_id"], run_id,
    )
    return RedirectResponse(f"/runs/{run_id}/interlink", status_code=303)


@app.get("/runs/{run_id}/interlink", response_class=HTMLResponse)
async def interlink_view(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    run = get_pipeline_run(run_id)
    cluster_id = run.get("cluster_id") if run else None

    snapshot = interlink_snapshot()
    interlink_running = (
        job_manager.is_active(f"{run_id}:interlink_cluster")
        or job_manager.is_active(f"{run_id}:interlink_global")
    )

    return templates.TemplateResponse(request, "_interlink.html", _ctx(user,
        run_id=run_id,
        cluster_id=cluster_id,
        snapshot=snapshot,
        interlink_running=interlink_running,
        active_page="pipeline",
    ))

@app.get("/runs/{run_id}/agent/{agent}/view", response_class=HTMLResponse)
async def agent_view(
    request: Request,
    run_id: str,
    agent: str,
    user: dict = Depends(require_user),
):
    inp = load_agent_input(run_id, agent)
    out = load_agent_output(run_id, agent)
    meta = load_agent_metadata(run_id, agent)
    console = load_agent_console(run_id, agent) or ""
    return templates.TemplateResponse(request, "_agent_view.html", _ctx(user,
        run_id=run_id,
        agent=agent,
        input=json.dumps(inp, indent=2, default=str) if inp else None,
        output=json.dumps(out, indent=2, default=str) if out else None,
        meta=meta or {},
        console=console[-8000:] if console else "",
    ))

@app.post("/interlink/anchor/{anchor_id}/approve")
async def interlink_approve(anchor_id: str, user: dict = Depends(require_user)):
    ok = approve_anchor(anchor_id)
    return JSONResponse({"ok": ok})


@app.post("/interlink/anchor/{anchor_id}/reject")
async def interlink_reject(anchor_id: str, user: dict = Depends(require_user)):
    ok = reject_anchor(anchor_id)
    return JSONResponse({"ok": ok})

@app.post("/runs/{run_id}/interlink/inject")
async def interlink_inject(
    run_id: str,
    dry_run: bool = Form(False),
    user: dict = Depends(require_user),
):
    results = inject_all_approved(dry_run=dry_run)
    return RedirectResponse(
        f"/runs/{run_id}/interlink?injected={results.get('injected', 0)}",
        status_code=303,
    )

@app.post("/interlink/anchor/{anchor_id}/edit")
async def interlink_edit(
    anchor_id: str,
    anchor_text: str = Form(...),
    user: dict = Depends(require_user),
):
    ok = edit_anchor_text(anchor_id, anchor_text)
    return JSONResponse({"ok": ok})


@app.post("/interlink/bulk/approve_all")
async def interlink_bulk_approve(user: dict = Depends(require_user)):
    """Approve every currently-pending anchor in one shot."""
    snap = interlink_snapshot(limit_pending=10000)
    n = 0
    for p in snap.pending:
        if not p.get("error") and approve_anchor(p["anchor_id"]):
            n += 1
    return JSONResponse({"ok": True, "approved": n})


@app.post("/interlink/bulk/reject_all")
async def interlink_bulk_reject(user: dict = Depends(require_user)):
    snap = interlink_snapshot(limit_pending=10000)
    n = 0
    for p in snap.pending:
        if not p.get("error") and reject_anchor(p["anchor_id"]):
            n += 1
    return JSONResponse({"ok": True, "rejected": n})


# ─── Topic queue ──────────────────────────────────────────────────────────

@app.get("/queue", response_class=HTMLResponse)
async def queue_view(request: Request, user: dict = Depends(require_user)):
    items = list_topic_queue(limit=100)
    return templates.TemplateResponse(request, "_queue.html", _ctx(user, items=items))


@app.post("/queue/add")
async def queue_add(
    topics: str = Form(...),
    user: dict = Depends(require_user),
):
    """Accepts a textarea of newline-separated topics."""
    added = 0
    for t in topics.splitlines():
        t = t.strip()
        if t:
            enqueue_topic(t, user["username"])
            added += 1
    return RedirectResponse(f"/queue?added={added}", status_code=303)


# ─── Admin (admin role only) ──────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: dict = Depends(require_admin)):
    from config_loader import get_config
    cfg = get_config()
    runs = list_pipeline_runs(limit=500)

    # Per-user activity rollup
    by_user = {}
    for r in runs:
        u = r.get("submitted_by") or "(unknown)"
        s = by_user.setdefault(u, {
            "runs": 0, "cost_usd": 0.0, "llm_calls": 0,
            "serp_calls": 0, "tokens_in": 0, "tokens_out": 0,
        })
        s["runs"] += 1
        s["cost_usd"]   += r.get("total_cost_usd", 0)   or 0
        s["llm_calls"]  += r.get("total_llm_calls", 0)  or 0
        s["serp_calls"] += r.get("total_serp_calls", 0) or 0
        s["tokens_in"]  += r.get("total_tokens_in", 0)  or 0
        s["tokens_out"] += r.get("total_tokens_out", 0) or 0

    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    recent = [r for r in runs if (r.get("created_at") or "") >= cutoff]
    week_total = {
        "runs": len(recent),
        "cost_usd":  sum(r.get("total_cost_usd", 0)   or 0 for r in recent),
        "llm_calls": sum(r.get("total_llm_calls", 0)  or 0 for r in recent),
    }

    # API key status — show last 4 chars only, never the full key
    def _mask(v):
        if not v: return ""
        return f"…{v[-4:]}" if len(v) >= 8 else "set"
    env_status = {
        "anthropic":  _mask(os.getenv("ANTHROPIC_API_KEY", "")),
        "serpapi":    _mask(os.getenv("SERPAPI_API_KEY", "")),
        "perplexity": _mask(os.getenv("PERPLEXITY_API_KEY", "")),
    }

    return templates.TemplateResponse(request, "_admin.html", _ctx(user,
        users=USERS,
        by_user=by_user,
        week_total=week_total,
        all_runs=runs[:50],
        env_status=env_status,
        thresholds=cfg.get("quality", {}),
        budget=cfg.get("budget", {}),
        active_page="admin",
    ))

@app.post("/admin/api-keys")
async def admin_save_api_keys(
    request: Request,
    user: dict = Depends(require_admin),
):
    """
    Update API keys at runtime. Persists to .env on disk and updates
    os.environ for the running process. Worker processes started by
    JobManager inherit the parent env, so newly-submitted jobs pick up
    the new value.
    """
    form = await request.form()
    updates = {
        "ANTHROPIC_API_KEY":  (form.get("anthropic_api_key")  or "").strip(),
        "SERPAPI_API_KEY":    (form.get("serpapi_api_key")    or "").strip(),
        "PERPLEXITY_API_KEY": (form.get("perplexity_api_key") or "").strip(),
    }

    # Apply to live env
    for k, v in updates.items():
        if v:                      # blank input = leave unchanged
            os.environ[k] = v

    # Persist to .env (preserve unrelated lines)
    env_path = ROOT / ".env"
    existing = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    for k, v in updates.items():
        if v:
            existing[k] = v

    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
        encoding="utf-8",
    )

    return RedirectResponse("/admin?saved=1", status_code=303)
# ─── Artifacts (read-only browser) ────────────────────────────────────────

@app.get("/runs/{run_id}/artifacts", response_class=HTMLResponse)
async def artifacts_view(
    request: Request,
    run_id: str,
    user: dict = Depends(require_user),
):
    artifacts = list_artifacts(run_id)
    return HTMLResponse(
        "<pre>" + json.dumps(artifacts, indent=2, default=str) + "</pre>"
    )
# ─── Ingestion ────────────────────────────────────────────────────────────

@app.get("/ingestion", response_class=HTMLResponse)
async def ingestion_view(request: Request, user: dict = Depends(require_user)):
    # Tolerant imports — these helpers may not all exist yet
    raw_facts, raw_pending = [], []
    try:
        from db.sqlite_ops import get_facts
        raw_facts = get_facts(limit=50) or []
    except Exception as e:
        print(f"[ingestion] get_facts failed: {e}")
    try:
        from db.sqlite_ops import get_pending_verifications
        raw_pending = get_pending_verifications(limit=20) or []
    except Exception as e:
        print(f"[ingestion] get_pending_verifications failed: {e}")

    def _g(d, *keys, default=""):
        """Get first non-empty key from dict-like d."""
        for k in keys:
            try:
                v = d[k] if isinstance(d, dict) else getattr(d, k, None)
            except (KeyError, AttributeError):
                v = None
            if v not in (None, ""):
                return v
        return default

    facts = [{
        "statement":  str(_g(f, "statement", "fact", "text", "content", "claim")),
        "source":     str(_g(f, "source", "source_url", "origin", "url")),
        "confidence": float(_g(f, "confidence", "score", default=0) or 0),
        "created_at": str(_g(f, "created_at", "timestamp", "date")),
    } for f in raw_facts]

    pending = [{
        "claim":      str(_g(p, "claim", "statement", "fact", "text")),
        "reason":     str(_g(p, "reason", "issue", "note")),
        "article_id": str(_g(p, "article_id", "article")),
    } for p in raw_pending]

    return templates.TemplateResponse(request, "_ingestion.html", _ctx(user,
        facts=facts,
        pending=pending,
    ))

@app.post("/ingestion/upload")
async def ingestion_upload(
    request: Request,
    user: dict = Depends(require_user),
):
    """Accepts a multipart upload of a research document and ingests it."""
    from fastapi import UploadFile
    import tempfile
    from ingestion.pipeline import ingest_document

    form = await request.form()
    upload: UploadFile = form.get("file")
    topic = (form.get("topic") or "").strip()
    source_url = (form.get("source_url") or "").strip()

    if not upload:
        raise HTTPException(400, "No file uploaded")

    # Save to a temp file
    suffix = Path(upload.filename or "").suffix.lower() or ".txt"
    if suffix not in (".md", ".txt", ".pdf", ".docx"):
        raise HTTPException(400, f"Unsupported file type: {suffix}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await upload.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        summary = ingest_document(
            tmp_path,
            topic=topic,
            source_url=source_url,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return JSONResponse({"ok": True, "summary": summary})


# ─── Knowledge Graph ──────────────────────────────────────────────────────

@app.get("/graph", response_class=HTMLResponse)
async def graph_view(
    request: Request,
    filter_type: str = "all",
    max_nodes: int = 200,
    user: dict = Depends(require_user),
):
    """Renders the knowledge graph as an interactive HTML page."""
    from db.graph_ops import load_graph, graph_stats, get_nodes_by_type
    from viz.graph_viewer import export_to_html
    import tempfile

    G = load_graph()
    stats = graph_stats(G)
    type_counts = {}
    for n, data in G.nodes(data=True):
        t = data.get("node_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Render the interactive vis.js HTML to a temp file, then read it back
    graph_html = ""
    if stats["total_nodes"] > 0:
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name
        try:
            export_to_html(
                tmp_path,
                filter_node_type=None if filter_type == "all" else filter_type,
                max_nodes=max_nodes,
            )
            graph_html = Path(tmp_path).read_text(encoding="utf-8")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return templates.TemplateResponse(request, "_graph.html", _ctx(user,
        stats=stats,
        type_counts=type_counts,
        filter_type=filter_type,
        max_nodes=max_nodes,
        graph_html=graph_html,
    ))


@app.get("/graph/iframe", response_class=HTMLResponse)
async def graph_iframe(
    filter_type: str = "all",
    max_nodes: int = 200,
    user: dict = Depends(require_user),
):
    """Returns just the graph HTML for embedding in an iframe."""
    from viz.graph_viewer import export_to_html
    import tempfile

    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp_path = tmp.name
    try:
        export_to_html(
            tmp_path,
            filter_node_type=None if filter_type == "all" else filter_type,
            max_nodes=max_nodes,
        )
        html = Path(tmp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return HTMLResponse(content=html)

# ─── Startup ──────────────────────────────────────────────────────────────

@app.on_event("startup")
def _startup():
    # start_scheduler() spawns its own daemon thread — call directly.
    start_scheduler()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)