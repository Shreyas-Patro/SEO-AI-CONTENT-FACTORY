"""
dashboard_components/output_viewer.py — lean version.

Improvements over v1:
  • All DB queries cached with TTL=5s
  • Pagination — render only 5 articles per page (configurable)
  • Lazy zip — built only when user clicks "Generate zip", not on every rerun
  • Pause-auto-refresh toggle — stops the runaway rerun loop when reading
  • Stage timeline only fetched when user opens article (uses session_state cache)
"""
import streamlit as st
import json
import io
import zipfile
from datetime import datetime

from db.sqlite_ops import db_conn


PAGE_SIZE = 5


# ────────────────────────────────────────────────────────────────────────────
# Cached DB layer
# ────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=5, show_spinner=False)
def _fetch_runs():
    with db_conn() as conn:
        cur = conn.execute(
            "SELECT id, topic, created_at, status FROM pipeline_runs "
            "ORDER BY created_at DESC LIMIT 50"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@st.cache_data(ttl=5, show_spinner=False)
def _fetch_clusters_for_run(run_id):
    with db_conn() as conn:
        if run_id and run_id != "all":
            cur = conn.execute(
                "SELECT cluster_id FROM pipeline_runs WHERE id = ?", (run_id,)
            )
            row = cur.fetchone()
            cluster_ids = [row[0]] if row and row[0] else []
        else:
            cur = conn.execute("SELECT id FROM clusters ORDER BY rowid DESC LIMIT 50")
            cluster_ids = [r[0] for r in cur.fetchall()]

        if not cluster_ids:
            return []
        placeholders = ",".join("?" for _ in cluster_ids)
        cur = conn.execute(
            f"SELECT * FROM clusters WHERE id IN ({placeholders})",
            cluster_ids,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@st.cache_data(ttl=5, show_spinner=False)
def _fetch_articles(cluster_id, status, atype):
    sql = "SELECT * FROM articles WHERE 1=1"
    params = []
    if cluster_id and cluster_id != "all":
        sql += " AND cluster_id = ?"
        params.append(cluster_id)
    if status and status != "all":
        sql += " AND status = ?"
        params.append(status)
    if atype and atype != "all":
        sql += " AND article_type = ?"
        params.append(atype)
    sql += " ORDER BY created_at DESC"

    with db_conn() as conn:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@st.cache_data(ttl=5, show_spinner=False)
def _fetch_stages(article_id):
    try:
        with db_conn() as conn:
            cur = conn.execute(
                "SELECT agent_name, status, started_at, finished_at, "
                "cost_usd, error FROM agent_runs WHERE article_id = ? "
                "ORDER BY started_at ASC",
                (article_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────────────────
# Markdown export
# ────────────────────────────────────────────────────────────────────────────
def _maybe_json(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return None


def _build_markdown(article):
    title = (article.get("title") or "Untitled").replace('"', '\\"')
    slug = article.get("slug") or "untitled"
    atype = article.get("article_type") or "spoke"
    target_kw = _maybe_json(article.get("target_keywords")) or {}
    meta = _maybe_json(article.get("meta")) or {}

    fm = ["---", f'title: "{title}"', f"slug: {slug}", f"url: /{slug}",
          f"article_type: {atype}"]
    if article.get("cluster_id"): fm.append(f"cluster_id: {article['cluster_id']}")
    if article.get("status"): fm.append(f"status: {article['status']}")
    if article.get("word_count"): fm.append(f"word_count: {article['word_count']}")
    if article.get("brand_score") is not None: fm.append(f"brand_score: {article['brand_score']}")
    if article.get("fact_score") is not None: fm.append(f"fact_score: {article['fact_score']}")
    if article.get("readability") is not None: fm.append(f"readability: {article['readability']}")
    if isinstance(target_kw, dict) and target_kw.get("primary"):
        fm.append(f"primary_keyword: {target_kw['primary']}")
    if isinstance(meta, dict) and meta.get("description"):
        fm.append(f'description: "{str(meta["description"]).replace(chr(34), chr(92)+chr(34))}"')
    fm.append("---\n")

    body = article.get("content_md") or article.get("content") or ""
    return "\n".join(fm) + "\n" + body


# ────────────────────────────────────────────────────────────────────────────
# Stage timeline
# ────────────────────────────────────────────────────────────────────────────
STAGE_ORDER = ["lead_writer", "fact_verifier", "brand_auditor", "rewriter", "meta_tagger"]
STAGE_EMOJI = {"lead_writer": "✍️", "fact_verifier": "🔍", "brand_auditor": "🎨",
               "rewriter": "🔄", "meta_tagger": "🏷️"}
STAGE_COLOR = {"completed": "#10b981", "running": "#3b82f6", "failed": "#ef4444"}


def _render_stage_timeline(stages):
    if not stages:
        st.caption("No stage history recorded yet.")
        return
    by_agent = {}
    for s in stages:
        by_agent.setdefault(s.get("agent_name", "?"), []).append(s)

    cols = st.columns(len(STAGE_ORDER))
    for i, agent in enumerate(STAGE_ORDER):
        with cols[i]:
            runs = by_agent.get(agent, [])
            emoji = STAGE_EMOJI.get(agent, "•")
            if not runs:
                st.markdown(
                    f"<div style='text-align:center;opacity:0.3'>"
                    f"<div style='font-size:22px'>{emoji}</div>"
                    f"<div style='font-size:11px'>{agent}</div>"
                    f"<div style='font-size:10px;color:#888'>not run</div></div>",
                    unsafe_allow_html=True)
            else:
                last = runs[-1]
                cnt = len(runs)
                color = STAGE_COLOR.get(last.get("status", "completed"), "#6b7280")
                badge = f" ×{cnt}" if cnt > 1 else ""
                st.markdown(
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:22px'>{emoji}</div>"
                    f"<div style='font-size:11px;font-weight:600'>{agent}{badge}</div>"
                    f"<div style='font-size:10px;color:{color}'>● {last.get('status')}</div>"
                    f"</div>",
                    unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────────────────────
# Article card (lean, lazy)
# ────────────────────────────────────────────────────────────────────────────
def _render_article_card(article):
    article_id = article.get("id")
    title = article.get("title", "Untitled")
    atype = article.get("article_type", "spoke")
    status = article.get("status", "draft")
    wc = article.get("word_count") or 0
    type_emoji = {"hub": "🏛️", "spoke": "📄", "sub_spoke": "📃", "faq": "❓"}.get(atype, "📄")
    status_emoji = {"published": "🟢", "approved": "🟡", "draft": "⚪",
                    "rewriting": "🔄", "failed": "🔴"}.get(status, "⚪")

    label = f"{type_emoji} {title}  ·  {status_emoji} {status}  ·  {wc} words"

    with st.expander(label, expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Brand", f"{article.get('brand_score') or 0:.1f}/10")
        fs = article.get("fact_score")
        c2.metric("Fact", f"{fs:.2f}" if fs is not None else "—")
        r = article.get("readability")
        c3.metric("Readability", f"{r:.0f}" if r is not None else "—")
        c4.metric("Iterations", article.get("iteration") or 0)

        # Build markdown lazily — only the bytes the user is about to download
        md_bytes = _build_markdown(article).encode("utf-8")
        slug = article.get("slug") or f"untitled-{(article_id or '')[:8]}"

        d1, d2 = st.columns([1, 1])
        with d1:
            st.download_button(
                "⬇️ Download .md",
                data=md_bytes,
                file_name=f"{slug}.md",
                mime="text/markdown",
                key=f"dl_md_{article_id}",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                "⬇️ Download .json",
                data=json.dumps(article, indent=2, default=str).encode("utf-8"),
                file_name=f"{slug}.json",
                mime="application/json",
                key=f"dl_json_{article_id}",
                use_container_width=True,
            )

        t1, t2, t3 = st.tabs(["📄 Content", "🔁 Stages", "🏷️ Meta"])

        with t1:
            content = article.get("content_md") or article.get("content") or ""
            if content.strip():
                st.markdown(content)
            else:
                st.info("No content yet.")

        with t2:
            # Stages are fetched only when this tab is rendered
            _render_stage_timeline(_fetch_stages(article_id))

        with t3:
            meta = _maybe_json(article.get("meta")) or {}
            if meta:
                st.code(json.dumps(meta, indent=2, default=str), language="json")
            else:
                st.caption("Meta tagger hasn't run yet.")


# ────────────────────────────────────────────────────────────────────────────
# Bulk zip — built lazily, cached per filter set
# ────────────────────────────────────────────────────────────────────────────
def _build_cluster_zip(articles):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for a in articles:
            content = a.get("content_md") or a.get("content") or ""
            if not content.strip():
                continue
            slug = a.get("slug") or f"untitled-{(a.get('id') or '')[:8]}"
            zf.writestr(f"{slug}.md", _build_markdown(a))
        manifest = [{
            "id": a.get("id"), "slug": a.get("slug"), "title": a.get("title"),
            "type": a.get("article_type"), "status": a.get("status"),
            "word_count": a.get("word_count"), "brand_score": a.get("brand_score"),
            "fact_score": a.get("fact_score"),
        } for a in articles]
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    buf.seek(0)
    return buf.read()


# ────────────────────────────────────────────────────────────────────────────
# Main render
# ────────────────────────────────────────────────────────────────────────────
def render_output_viewer(monitor=None):
    st.markdown("### 📦 Article Output Viewer")
    st.caption("Browse, read, and download finished articles.")

    # Pause auto-refresh control — Streamlit's autorefresh is global, but if
    # we set a session flag the dashboard.py block can check it and skip rerun.
    pcol1, pcol2, pcol3 = st.columns([2, 1, 1])
    with pcol1:
        st.session_state["pause_autorefresh"] = st.checkbox(
            "⏸ Pause auto-refresh while reading",
            value=st.session_state.get("pause_autorefresh", True),
            key="ov_pause_autorefresh",
        )
    with pcol2:
        if st.button("🔄 Refresh data", key="ov_refresh", use_container_width=True):
            _fetch_runs.clear()
            _fetch_clusters_for_run.clear()
            _fetch_articles.clear()
            _fetch_stages.clear()
            st.rerun()
    with pcol3:
        st.caption(f"Cache TTL: 5s")

    # Filters
    runs = _fetch_runs()
    run_options = ["all"] + [r["id"] for r in runs]
    run_labels = {"all": "All runs"}
    for r in runs:
        ts = (r.get("created_at") or "")[:16]
        run_labels[r["id"]] = f"{r.get('topic','?')} · {ts}"

    f1, f2, f3, f4 = st.columns([3, 2, 2, 2])
    with f1:
        run_filter = st.selectbox(
            "Run", run_options,
            format_func=lambda x: run_labels.get(x, x),
            key="ov_run_filter",
        )
    with f2:
        clusters = _fetch_clusters_for_run(run_filter)
        cluster_options = ["all"] + [c["id"] for c in clusters]
        cluster_labels = {"all": "All clusters"}
        for c in clusters:
            cluster_labels[c["id"]] = c.get("topic") or c.get("name") or str(c.get("id"))[:12]
        cluster_filter = st.selectbox(
            "Cluster", cluster_options,
            format_func=lambda x: cluster_labels.get(x, x),
            key="ov_cluster_filter",
        )
    with f3:
        status_filter = st.selectbox(
            "Status", ["all", "draft", "rewriting", "approved", "published", "failed"],
            key="ov_status_filter",
        )
    with f4:
        type_filter = st.selectbox(
            "Type", ["all", "hub", "spoke", "sub_spoke", "faq"],
            key="ov_type_filter",
        )

    articles = _fetch_articles(cluster_filter, status_filter, type_filter)
    total = len(articles)
    written = sum(1 for a in articles if (a.get("content_md") or a.get("content") or "").strip())
    total_words = sum(a.get("word_count") or 0 for a in articles)

    # Summary bar
    s1, s2, s3, s4 = st.columns([1, 1, 1, 2])
    s1.metric("Articles", total)
    s2.metric("Written", written)
    s3.metric("Total words", f"{total_words:,}")
    with s4:
        # Lazy zip — only built when user clicks the button.
        # First click re-renders with `_show_zip=True` and builds + offers download.
        zip_state_key = f"zip_ready_{cluster_filter}_{status_filter}_{type_filter}"
        if not st.session_state.get(zip_state_key):
            if written > 0 and st.button("📦 Generate .zip", key="ov_gen_zip",
                                          use_container_width=True):
                st.session_state[zip_state_key] = _build_cluster_zip(articles)
                st.rerun()
        else:
            st.download_button(
                "⬇️ Download .zip",
                data=st.session_state[zip_state_key],
                file_name=f"articles_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                mime="application/zip",
                key="ov_dl_zip",
                use_container_width=True,
            )

    st.divider()

    if not articles:
        st.info("No articles match these filters.")
        return

    # Pagination
    page_key = "ov_page"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    n_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if st.session_state[page_key] >= n_pages:
        st.session_state[page_key] = 0

    pc1, pc2, pc3 = st.columns([1, 3, 1])
    with pc1:
        if st.button("◀ Prev", disabled=(st.session_state[page_key] == 0),
                      use_container_width=True):
            st.session_state[page_key] -= 1
            st.rerun()
    with pc2:
        st.caption(f"Page {st.session_state[page_key] + 1} of {n_pages}  ·  "
                   f"showing articles {st.session_state[page_key]*PAGE_SIZE + 1}–"
                   f"{min((st.session_state[page_key]+1)*PAGE_SIZE, total)}")
    with pc3:
        if st.button("Next ▶", disabled=(st.session_state[page_key] >= n_pages - 1),
                      use_container_width=True):
            st.session_state[page_key] += 1
            st.rerun()

    start = st.session_state[page_key] * PAGE_SIZE
    end = start + PAGE_SIZE
    for article in articles[start:end]:
        _render_article_card(article)