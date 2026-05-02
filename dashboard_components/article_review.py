"""
dashboard_components/article_review.py

DB-direct version. Queries articles straight from canvas.db so scores
always reflect the latest agent run. Uses tabs (not nested expanders)
to avoid Streamlit nesting violations. Cached with TTL=3s for performance.
"""
import streamlit as st
import json

from db.sqlite_ops import db_conn


@st.cache_data(ttl=3, show_spinner=False)
def _fetch_articles_for_run(run_id):
    with db_conn() as conn:
        if run_id:
            cur = conn.execute(
                "SELECT cluster_id FROM pipeline_runs WHERE id = ?", (run_id,)
            )
            row = cur.fetchone()
            cluster_id = row[0] if row else None
            if cluster_id:
                cur = conn.execute(
                    "SELECT * FROM articles WHERE cluster_id = ? ORDER BY created_at DESC",
                    (cluster_id,),
                )
            else:
                return []
        else:
            cur = conn.execute("SELECT * FROM articles ORDER BY created_at DESC LIMIT 50")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    for r in rows:
        for k in ("target_keywords", "outline", "meta", "flagged_claims", "brand_flags"):
            v = r.get(k)
            if isinstance(v, str) and v.strip().startswith(("{", "[")):
                try:
                    r[k] = json.loads(v)
                except Exception:
                    pass
    return rows


@st.cache_data(ttl=3, show_spinner=False)
def _fetch_history(article_id):
    try:
        with db_conn() as conn:
            cur = conn.execute(
                "SELECT agent_name, status, started_at, finished_at, "
                "cost_usd, error FROM agent_runs WHERE article_id = ? "
                "ORDER BY started_at DESC LIMIT 30",
                (article_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def _pill(label, score, scale="10"):
    if score is None:
        return f'<span class="score-pill score-na">{label}: —</span>'
    try:
        v = float(score)
    except Exception:
        return f'<span class="score-pill score-na">{label}: —</span>'
    if scale == "1":
        cls = "score-good" if v >= 0.85 else "score-warn" if v >= 0.7 else "score-bad"
        return f'<span class="score-pill {cls}">{label}: {v:.2f}</span>'
    cls = "score-good" if v >= 7.0 else "score-warn" if v >= 5.0 else "score-bad"
    return f'<span class="score-pill {cls}">{label}: {v:.1f}</span>'


def _readability_pill(score):
    if score is None:
        return '<span class="score-pill score-na">Read: —</span>'
    try:
        v = float(score)
    except Exception:
        return '<span class="score-pill score-na">Read: —</span>'
    cls = "score-good" if v >= 60 else "score-warn" if v >= 45 else "score-bad"
    return f'<span class="score-pill {cls}">Read: {v:.0f}</span>'


def _action_buttons(m, article_id, run_id):
    cols = st.columns(4)
    for col, (label, agent, prefix) in zip(cols, [
        (" Rewrite", "rewriter", "rw"),
        (" Verify", "fact_verifier", "fv"),
        (" Audit", "brand_auditor", "ba"),
        (" Meta", "meta_tagger", "mt"),
    ]):
        with col:
            if st.button(label, key=f"{prefix}_{article_id}", use_container_width=True):
                try:
                    m["rerun_agent"](run_id, agent, article_id=article_id)
                    st.success(f"{agent} queued")
                    _fetch_articles_for_run.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed: {e}")


def _render_article_tabs(m, article, run_id):
    article_id = article.get("id")
    content = article.get("content_md") or article.get("content") or ""
    flagged = article.get("flagged_claims") or []
    brand_flags = article.get("brand_flags") or []
    meta = article.get("meta") or {}

    t_over, t_content, t_issues, t_hist, t_meta = st.tabs(
        ["Overview", " Content", "⚠️ Issues", "History", " Meta"]
    )

    with t_over:
        c = st.columns(4)
        c[0].metric("Word count", article.get("word_count") or 0)
        bs = article.get("brand_score")
        c[1].metric("Brand", f"{bs:.1f}/10" if bs is not None else "—")
        fs = article.get("fact_score")
        c[2].metric("Fact", f"{fs:.2f}" if fs is not None else "—")
        r = article.get("readability")
        c[3].metric("Readability", f"{r:.0f}" if r is not None else "—")

        st.divider()
        if run_id:
            st.caption("Re-run a single agent on this article")
            _action_buttons(m, article_id, run_id)
        else:
            st.caption("⚠️ No active run — open one in the sidebar to enable rerun buttons")

        st.divider()
        c = st.columns(3)
        c[0].metric("Iterations", article.get("iteration") or 0)
        c[1].metric("Rewrites", article.get("rewrite_count") or 0)
        status = article.get("status") or "draft"
        emoji = {"published": "🟢", "approved": "🟡", "draft": "⚪", "rewriting": "🔄"}.get(status, "⚪")
        c[2].metric("Status", f"{emoji} {status}")

    with t_content:
        if content.strip():
            st.caption(f"{len(content.split())} words · {len(content):,} chars")
            st.markdown(content)
        else:
            st.info("No content yet — article hasn't been written.")

    with t_issues:
        cf, cb = st.columns(2)
        with cf:
            st.markdown("**🔍 Flagged claims**")
            if flagged:
                for i, claim in enumerate(flagged[:20]):
                    if isinstance(claim, dict):
                        txt = claim.get("text") or claim.get("claim") or str(claim)
                        reason = claim.get("reason") or claim.get("issue") or ""
                    else:
                        txt = str(claim); reason = ""
                    st.markdown(f"`{i+1}.` {txt}")
                    if reason:
                        st.caption(f"   → {reason}")
            else:
                st.success("✓ No flagged claims")
        with cb:
            st.markdown("** Brand issues**")
            if brand_flags:
                for i, f in enumerate(brand_flags[:20]):
                    if isinstance(f, dict):
                        txt = f.get("passage") or f.get("text") or str(f)
                        issue = f.get("issue") or f.get("reason") or ""
                        sug = f.get("suggestion") or ""
                    else:
                        txt = str(f); issue = ""; sug = ""
                    st.markdown(f"`{i+1}.` {txt[:200]}")
                    if issue:
                        st.caption(f"   → {issue}")
                    if sug:
                        st.caption(f"   ✏️ {sug}")
            else:
                st.success("✓ No brand issues")

    with t_hist:
        history = _fetch_history(article_id)
        if history:
            for entry in history:
                ts = (entry.get("started_at") or "")[:19]
                agent = entry.get("agent_name", "?")
                status = entry.get("status", "")
                cost = entry.get("cost_usd") or 0
                line = f"`{ts}` **{agent}** — {status}"
                if cost:
                    line += f" · ${float(cost):.4f}"
                st.markdown(line)
                if entry.get("error"):
                    st.caption(f"   ❌ {entry['error'][:200]}")
        else:
            st.caption("No history recorded yet")

    with t_meta:
        if meta:
            st.code(json.dumps(meta, indent=2, default=str), language="json")
        else:
            st.info("Meta tagger hasn't run yet on this article.")


def render_article_review(m):
    """Render the article review panel. `m` is the loaded modules dict from dashboard.py."""
    run_id = st.session_state.get("viewing_run_id") or st.session_state.get("current_run_id")

    rcol1, rcol2 = st.columns([6, 1])
    with rcol1:
        if run_id:
            st.caption(f"Reviewing run: `{run_id}`")
        else:
            st.caption("No run selected — showing latest 50 articles across all clusters")
    with rcol2:
        if st.button("🔄 Refresh", key="ar_refresh", use_container_width=True):
            _fetch_articles_for_run.clear()
            _fetch_history.clear()
            st.rerun()

    articles = _fetch_articles_for_run(run_id)

    if not articles:
        st.info("No articles yet. Run Layer 2 → Layer 3 to generate them.")
        return

    f1, f2, f3 = st.columns([2, 2, 1])
    with f1:
        filter_status = st.selectbox(
            "Status",
            ["all", "draft", "rewriting", "approved", "published"],
            key="ar_status_filter",
        )
    with f2:
        sort_by = st.selectbox(
            "Sort by",
            ["title", "brand_score", "fact_score", "readability", "iteration"],
            key="ar_sort_by",
        )
    with f3:
        st.caption(f"**{len(articles)} articles**")

    if filter_status != "all":
        articles = [a for a in articles if a.get("status") == filter_status]

    def sort_key(a):
        v = a.get(sort_by)
        if v is None:
            return -1 if sort_by != "title" else "zzz"
        return v
    articles = sorted(articles, key=sort_key, reverse=(sort_by != "title"))

    for article in articles:
        title = article.get("title", "Untitled")
        atype = article.get("article_type", "spoke")
        emoji = {"hub": "🏛️", "spoke": "📄", "sub_spoke": "📃", "faq": "❓"}.get(atype, "📄")
        pills = " ".join([
            _pill("Brand", article.get("brand_score"), "10"),
            _pill("Fact", article.get("fact_score"), "1"),
            _readability_pill(article.get("readability")),
        ])
        with st.expander(f"{emoji} {title}", expanded=False):
            st.markdown(pills, unsafe_allow_html=True)
            st.markdown("")
            _render_article_tabs(m, article, run_id)