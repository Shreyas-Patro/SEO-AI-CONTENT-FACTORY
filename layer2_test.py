"""
Canvas Homes — AI Agent Pipeline Dashboard (v2)

Now uses the pipeline orchestrator + artifact store, so:
- Every agent output is persisted to disk (Q1, Q4, Q10)
- You can browse all past pipeline runs
- You can inspect every agent's input AND output (Q1, Q5)
- You can edit any agent's output before the next agent runs (Q11)
- SERP/LLM call counts are visible per agent (Q3)
- Competitor Spy now shows ALL competitors (Q6)

Run from project root:  streamlit run dashboard.py
"""

import streamlit as st
import sys, os, json, io, contextlib
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Canvas Homes · AI Pipeline",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── STYLES (kept compact — same as before, trimmed) ────────────────────────
st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
.block-container { padding: 1.2rem 2rem 2rem; }
section[data-testid="stSidebar"] { background: #0f0f1a; border-right: 1px solid #1f1f35; }
section[data-testid="stSidebar"] .stButton button {
    background: #c8ff00; color: #000 !important; font-weight: 700;
    border: none; border-radius: 8px; width: 100%; font-size: 13px;
}
[data-testid="metric-container"] {
    background: #161624; border: 1px solid #1f1f35;
    border-radius: 10px; padding: 14px 16px;
}
.badge { display: inline-block; padding: 3px 10px; border-radius: 4px;
    font-family: 'Space Mono', monospace; font-size: 10px; font-weight: 700;
    letter-spacing: 0.08em; text-transform: uppercase; }
.badge-pending { background: rgba(136,136,170,0.12); color: #8888aa; }
.badge-running { background: rgba(0,212,255,0.12); color: #00d4ff; }
.badge-done { background: rgba(200,255,0,0.12); color: #aacc00; }
.badge-error { background: rgba(255,68,68,0.12); color: #ff6b6b; }
.console-box {
    background: #050508; border: 1px solid #1f1f35; border-radius: 8px;
    padding: 14px 16px; font-family: 'Space Mono', monospace; font-size: 11px;
    line-height: 1.8; color: #8888aa; max-height: 320px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all;
}
.log-info { color: #5a9fff; }
.log-success { color: #00ff88; }
.log-warn { color: #ffcc00; }
.log-error { color: #ff6b6b; }
.section-label {
    font-family: 'Space Mono', monospace; font-size: 9px; color: #8888aa;
    letter-spacing: 0.15em; text-transform: uppercase;
    border-bottom: 1px solid #1f1f35; padding-bottom: 6px; margin-bottom: 12px;
}
.data-card {
    background: #161624; border: 1px solid #1f1f35; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 8px;
}
.matrix-cell {
    display: inline-block; padding: 4px 10px; border-radius: 4px;
    font-family: 'Space Mono', monospace; font-size: 10px; font-weight: 700;
    margin: 2px;
}
.matrix-yes { background: rgba(0,255,136,0.15); color: #00ff88; }
.matrix-no  { background: rgba(255,68,68,0.12); color: #ff6b6b; }
.matrix-partial { background: rgba(255,204,0,0.15); color: #ffcc44; }
</style>
""", unsafe_allow_html=True)

# ── SESSION STATE ──────────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "topic": "Hosa Road",
        "current_run_id": None,
        "console_lines": [],
        "viewing_run_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init_state()


def clog(msg, kind="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.console_lines.append((ts, msg, kind))


def render_console():
    lines = st.session_state.console_lines[-150:]
    html = '<div class="console-box">'
    for ts, msg, kind in lines:
        cls = f"log-{kind}"
        safe = msg.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        html += f'<span style="color:#444;">[{ts}]</span> <span class="{cls}">{safe}</span>\n'
    html += "</div>"
    return html


class _LogCapture(io.StringIO):
    def write(self, s):
        super().write(s)
        s = s.strip()
        if not s:
            return
        kind = "info"
        if any(x in s for x in ["✅", "complete", "Complete"]):  kind = "success"
        elif any(x in s for x in ["❌", "Error", "error", "Failed"]): kind = "error"
        elif any(x in s for x in ["⚠️", "Warning"]):  kind = "warn"
        clog(s, kind)


@contextlib.contextmanager
def capture_logs():
    cap = _LogCapture()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = cap
    sys.stderr = cap
    try:
        yield cap
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


# ── LOAD MODULES ───────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _load():
    try:
        from orchestrator import (
            start_pipeline_run, run_layer1, run_layer2,
            approve_gate, reject_gate,
            load_agent_output, load_agent_input, load_agent_metadata,
            edit_agent_output, get_full_run_state
        )
        from db.artifacts import list_pipeline_runs, get_pipeline_run
        from db.sqlite_ops import get_stats, list_clusters, get_articles_by_cluster
        return {
            "start_pipeline_run": start_pipeline_run,
            "run_layer1": run_layer1,
            "run_layer2": run_layer2,
            "approve_gate": approve_gate,
            "reject_gate": reject_gate,
            "load_agent_output": load_agent_output,
            "load_agent_input": load_agent_input,
            "load_agent_metadata": load_agent_metadata,
            "edit_agent_output": edit_agent_output,
            "get_full_run_state": get_full_run_state,
            "list_pipeline_runs": list_pipeline_runs,
            "get_pipeline_run": get_pipeline_run,
            "get_stats": get_stats,
            "list_clusters": list_clusters,
            "get_articles_by_cluster": get_articles_by_cluster,
        }
    except Exception as e:
        return {"error": str(e)}

m = _load()
if "error" in m:
    st.error(f"Module load error: {m['error']}")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.caption("AI AGENT PIPELINE v2")

    st.markdown("**New Pipeline Run**")
    topic = st.text_input("Topic", value=st.session_state.topic, key="topic_input")
    st.session_state.topic = topic

    if st.button("▶ Start New Run", type="primary"):
        run_id = m["start_pipeline_run"](topic)
        st.session_state.current_run_id = run_id
        st.session_state.viewing_run_id = run_id
        st.session_state.console_lines = []
        clog(f"Started pipeline run {run_id} for '{topic}'", "success")
        st.rerun()

    st.markdown("---")

    # Past runs
    st.markdown("**Past Runs**")
    runs = m["list_pipeline_runs"](limit=20)
    if not runs:
        st.caption("No runs yet")
    else:
        run_labels = [
            f"{r['id'][-8:]} · {r['topic'][:18]} · {r['status']}"
            for r in runs
        ]
        idx = st.selectbox(
            "View previous run",
            range(len(runs)),
            format_func=lambda i: run_labels[i],
            label_visibility="collapsed"
        )
        if st.button("📂 Open this run"):
            st.session_state.viewing_run_id = runs[idx]["id"]
            st.session_state.current_run_id = runs[idx]["id"]
            st.rerun()

    st.markdown("---")
    if st.session_state.current_run_id:
        run = m["get_pipeline_run"](st.session_state.current_run_id)
        if run:
            st.caption(f"Current: `{run['id'][-8:]}`")
            st.markdown(f"**Status:** {run['status']}")
            st.markdown(f"**Stage:** {run.get('current_stage','?')}")
            st.markdown(f"**Cost:** ${run.get('total_cost_usd',0):.4f}")
            st.markdown(f"**SERP calls:** {run.get('total_serp_calls',0)}")
            st.markdown(f"**LLM calls:** {run.get('total_llm_calls',0)}")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
st.markdown("# AI Agent Pipeline")

view_run_id = st.session_state.viewing_run_id

if not view_run_id:
    st.info("Start a new pipeline run from the sidebar, or open a past run.")
    st.stop()

run = m["get_pipeline_run"](view_run_id)
if not run:
    st.error("Run not found")
    st.stop()


# ── Top control bar ───────────────────────────────────────────────────────
col1, col2, col3 = st.columns([2, 1, 1])
col1.markdown(f"**Topic:** `{run['topic']}`  ·  **Run:** `{view_run_id}`")

# Run buttons depend on stage
stage = run.get("current_stage", "init")
gate = run.get("gate_status", "pending")
status = run.get("status", "running")

with col2:
    if status == "running" and stage in ("init", "_pipeline"):
        if st.button("▶ Run Layer 1", type="primary"):
            with st.spinner("Running Layer 1: Trend Scout → Competitor Spy → Keyword Mapper..."):
                with capture_logs():
                    try:
                        m["run_layer1"](view_run_id)
                        clog("✅ Layer 1 complete. Awaiting gate.", "success")
                    except Exception as e:
                        clog(f"❌ Layer 1 failed: {e}", "error")
            st.rerun()
    elif gate == "pending" and stage == "gate_pending":
        st.success("Layer 1 done — review and approve below")
    elif gate == "approved" and status == "running":
        if st.button("▶ Run Layer 2", type="primary"):
            with st.spinner("Running Layer 2: Content Arch → FAQ Arch → Research Prompt Gen..."):
                with capture_logs():
                    try:
                        m["run_layer2"](view_run_id)
                        clog("✅ Layer 2 complete.", "success")
                    except Exception as e:
                        clog(f"❌ Layer 2 failed: {e}", "error")
            st.rerun()
    elif status == "completed":
        st.success("✅ Pipeline complete")

with col3:
    if gate == "pending" and stage == "gate_pending":
        if st.button("✅ Approve Gate", type="primary"):
            m["approve_gate"](view_run_id)
            clog("Gate approved — Layer 2 ready", "success")
            st.rerun()


# ── Console ───────────────────────────────────────────────────────────────
with st.expander("🖥️ CLI Console", expanded=True):
    st.markdown(render_console(), unsafe_allow_html=True)


st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════
# AGENT OUTPUT TABS
# ══════════════════════════════════════════════════════════════════════════

agent_tabs = st.tabs([
    "📡 Trend Scout", "🕵️ Competitor Spy", "🗺️ Keyword Mapper",
    "🚪 Gate", "🏗️ Content Architect", "❓ FAQ Architect",
    "🔬 Research Prompt", "🗂️ All Artifacts"
])


def render_agent_section(agent_key, tab_index):
    """Generic renderer for any agent's input/output/metadata."""
    inp = m["load_agent_input"](view_run_id, agent_key)
    out = m["load_agent_output"](view_run_id, agent_key)
    meta = m["load_agent_metadata"](view_run_id, agent_key)

    if not out and not inp:
        st.info(f"{agent_key} has not run yet for this pipeline run.")
        return

    # Metrics row
    if meta and isinstance(meta, dict):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Cost", f"${meta.get('cost_usd', 0):.4f}")
        c2.metric("LLM calls", meta.get("llm_calls", 0))
        c3.metric("SERP calls", meta.get("serp_calls", 0))
        c4.metric("Validation", "✓" if meta.get("validation_passed") else "✗ partial")

        if meta.get("validation_problems"):
            with st.expander("⚠️ Validation problems detected"):
                for p in meta["validation_problems"]:
                    st.warning(p)

    # Input
    with st.expander("📥 Input", expanded=False):
        if inp:
            st.json(inp)
        else:
            st.caption("No input artifact")

    # Output
    st.markdown('<div class="section-label">Output</div>', unsafe_allow_html=True)
    if out:
        # Provide an "edit" button (Q11)
        edit_key = f"edit_{agent_key}_{view_run_id}"
        if st.button(f"✏️ Edit this output", key=f"btn_{edit_key}"):
            st.session_state[edit_key] = True

        if st.session_state.get(edit_key):
            edited = st.text_area(
                "Edit JSON (will overwrite the output artifact):",
                value=json.dumps(out, indent=2),
                height=400,
                key=f"ta_{edit_key}"
            )
            col_save, col_cancel = st.columns(2)
            if col_save.button("💾 Save", key=f"save_{edit_key}"):
                try:
                    new_data = json.loads(edited)
                    m["edit_agent_output"](view_run_id, agent_key, new_data)
                    st.session_state[edit_key] = False
                    st.success("Saved. The next agent will read this version.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Invalid JSON: {e}")
            if col_cancel.button("Cancel", key=f"cancel_{edit_key}"):
                st.session_state[edit_key] = False
                st.rerun()
        else:
            st.json(out)
    else:
        st.caption("No output artifact")


# ── Tab 0: Trend Scout ────────────────────────────────────────────────────
with agent_tabs[0]:
    st.markdown("### Trend Scout")
    out = m["load_agent_output"](view_run_id, "trend_scout")
    if out:
        analysis = out.get("analysis", {})
        raw = out.get("raw_data", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("PAA Questions", len(raw.get("paa_questions", [])))
        c2.metric("Related Searches", len(raw.get("related_searches", [])))
        c3.metric("Autocomplete", len(raw.get("autocomplete", [])))
        c4.metric("AEO Targets", len(analysis.get("aeo_targets", [])))

        if analysis.get("top_5_priority_queries"):
            st.markdown('<div class="section-label">Top 5 Priority Queries (Quick Wins context)</div>', unsafe_allow_html=True)
            for i, q in enumerate(analysis["top_5_priority_queries"], 1):
                st.markdown(f"""
                <div class="data-card">
                    <strong>#{i}: {q.get('query','—')}</strong><br>
                    <small>{q.get('reason','')}</small>
                </div>""", unsafe_allow_html=True)

        if analysis.get("content_gaps"):
            st.markdown('<div class="section-label">Content Gaps</div>', unsafe_allow_html=True)
            for g in analysis["content_gaps"][:5]:
                st.markdown(f"**[{g.get('priority','?').upper()}]** {g.get('gap','')}")
                st.caption(f"→ {g.get('opportunity','')}")
    render_agent_section("trend_scout", 0)


# ── Tab 1: Competitor Spy ─────────────────────────────────────────────────
with agent_tabs[1]:
    st.markdown("### Competitor Spy")
    out = m["load_agent_output"](view_run_id, "competitor_spy")
    if out:
        cov = out.get("competitor_coverage", [])
        gaps = out.get("coverage_gaps", [])
        raw = out.get("raw_results", {})

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Competitors Searched", len(raw))
        c2.metric("Competitors Analyzed", len(cov))
        c3.metric("Total Articles", sum(len(v) for v in raw.values()))
        c4.metric("Coverage Gaps", len(gaps))

        # ─── COMPETITOR COVERAGE MATRIX (Q-bonus 3) ───────────────────
        st.markdown("---")
        st.markdown('<div class="section-label">Competitor Coverage Matrix</div>', unsafe_allow_html=True)

        # Categorize topics by what each competitor covers
        topic_categories = ["Locality Guide", "Property Listings", "Rental Market",
                           "Pricing", "Connectivity", "Lifestyle/Amenities",
                           "Legal/Documentation", "Investment Analysis"]

        # Build a matrix from raw results — rough categorization by snippet keywords
        competitor_names = list(raw.keys())
        if competitor_names:
            header_cols = st.columns([2] + [1] * len(competitor_names))
            header_cols[0].markdown("**Topic Category**")
            for i, c in enumerate(competitor_names):
                header_cols[i + 1].markdown(f"**{c.split('.')[0]}**")

            for cat in topic_categories:
                cat_kw = cat.lower().split("/")[0].split()[0]
                row_cols = st.columns([2] + [1] * len(competitor_names))
                row_cols[0].caption(cat)
                for i, c in enumerate(competitor_names):
                    arts = raw[c]
                    has_coverage = any(
                        cat_kw in (a.get("title", "") + a.get("snippet", "")).lower()
                        for a in arts
                    )
                    cell = '<span class="matrix-cell matrix-yes">✓</span>' if has_coverage else '<span class="matrix-cell matrix-no">✗</span>'
                    row_cols[i + 1].markdown(cell, unsafe_allow_html=True)

        # ─── DETAILED COMPETITOR BREAKDOWN ────────────────────────────
        st.markdown("---")
        st.markdown('<div class="section-label">Per-Competitor Articles (Raw)</div>', unsafe_allow_html=True)
        for comp_name, articles in raw.items():
            with st.expander(f"**{comp_name}** — {len(articles)} articles found"):
                if not articles:
                    st.caption("No articles found for this topic")
                else:
                    for art in articles:
                        st.markdown(f"**{art.get('title','—')}**")
                        st.caption(art.get("link", ""))
                        st.write(art.get("snippet", "")[:200])
                        st.markdown("---")

        # Coverage gaps
        if gaps:
            st.markdown('<div class="section-label">Coverage Gaps — All Competitors Miss</div>', unsafe_allow_html=True)
            for g in gaps:
                pri = g.get("priority", "medium").upper()
                icon = {"HIGH":"🔴","MEDIUM":"🟡","LOW":"🟢"}.get(pri,"⚪")
                st.markdown(f"{icon} **[{pri}]** {g.get('gap','')}")
                st.caption(f"Suggested type: {g.get('suggested_article_type','?')}")

    render_agent_section("competitor_spy", 1)


# ── Tab 2: Keyword Mapper ─────────────────────────────────────────────────
with agent_tabs[2]:
    st.markdown("### Keyword Mapper")
    out = m["load_agent_output"](view_run_id, "keyword_mapper")
    if out:
        groups = out.get("keyword_groups", [])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Keywords", out.get("total_keywords", 0))
        c2.metric("Article Groups", len(groups))
        c3.metric("Quick Wins", len(out.get("quick_win_keywords", [])))
        c4.metric("Strategic KWs", len(out.get("strategic_keywords", [])))

        st.markdown('<div class="section-label">Keyword Groups</div>', unsafe_allow_html=True)
        for g in groups:
            with st.expander(f"**{g.get('group_name','—')}** · {g.get('suggested_article_type','?')} · {g.get('priority','?')}"):
                st.markdown(f"**Primary:** `{g.get('primary_keyword','—')}`")
                st.markdown(f"**Secondary:** {', '.join(g.get('secondary_keywords', []))}")
                st.markdown(f"**Long-tail:** {', '.join(g.get('long_tail_keywords', [])[:5])}")
                st.caption(f"Difficulty: {g.get('difficulty','?')} | Volume: {g.get('estimated_volume','?')}")

    render_agent_section("keyword_mapper", 2)


# ── Tab 3: Gate ───────────────────────────────────────────────────────────
with agent_tabs[3]:
    st.markdown("### Human Approval Gate")
    if gate == "pending" and stage == "gate_pending":
        st.warning("Layer 1 complete. Review the outputs above, edit if needed, then approve.")
        c1, c2 = st.columns(2)
        if c1.button("✅ Approve and proceed to Layer 2", type="primary", key="gate_approve_main"):
            m["approve_gate"](view_run_id)
            st.rerun()
        if c2.button("✗ Reject and stop", key="gate_reject_main"):
            m["reject_gate"](view_run_id)
            st.rerun()
    elif gate == "approved":
        st.success("Gate approved. Layer 2 can now run.")
    elif gate == "rejected":
        st.error("Gate was rejected. This pipeline run is cancelled.")
    else:
        st.info("Gate is not yet active. Run Layer 1 first.")


# ── Tab 4: Content Architect ──────────────────────────────────────────────
with agent_tabs[4]:
    st.markdown("### Content Architect")
    out = m["load_agent_output"](view_run_id, "content_architect")
    if out:
        plan = out.get("cluster_plan", {})
        articles = plan.get("articles", [])
        hubs = [a for a in articles if a.get("type") == "hub"]
        spokes = [a for a in articles if a.get("type") == "spoke"]
        subs = [a for a in articles if a.get("type") == "sub_spoke"]
        faqs_a = [a for a in articles if a.get("type") == "faq"]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total", len(articles))
        c2.metric("Hubs", len(hubs))
        c3.metric("Spokes", len(spokes))
        c4.metric("Sub-spokes", len(subs))
        c5.metric("FAQ pages", len(faqs_a))

        for art in articles:
            with st.expander(f"**[{art.get('type','?').upper()}]** {art.get('title','—')}"):
                st.markdown(f"**Slug:** `/{art.get('slug','—')}`")
                st.markdown(f"**Primary keyword:** `{art.get('target_keywords',{}).get('primary','—')}`")
                st.markdown(f"**Words:** {art.get('word_count_target','?')}")
                st.markdown("**Outline:**")
                for h in art.get("outline", []):
                    st.markdown(f"- {h}")
    render_agent_section("content_architect", 4)


# ── Tab 5: FAQ Architect ──────────────────────────────────────────────────
with agent_tabs[5]:
    st.markdown("### FAQ Architect")
    out = m["load_agent_output"](view_run_id, "faq_architect")
    if out:
        faqs_by = out.get("faqs_by_article", {})
        c1, c2 = st.columns(2)
        c1.metric("Articles processed", out.get("total_articles", 0))
        c2.metric("Total FAQs", out.get("total_faqs", 0))

        for art_id, faqs in faqs_by.items():
            with st.expander(f"Article `{art_id}` — {len(faqs)} FAQs"):
                for f in faqs:
                    st.markdown(f"**Q:** {f.get('question','—')}")
                    st.markdown(f"**A:** {f.get('answer','—')}")
                    st.caption(f"Target: `{f.get('target_keyword','—')}`")
                    st.markdown("---")
    render_agent_section("faq_architect", 5)


# ── Tab 6: Research Prompt Generator ──────────────────────────────────────
with agent_tabs[6]:
    st.markdown("### Research Prompt Generator (NEW)")
    out = m["load_agent_output"](view_run_id, "research_prompt_generator")
    if out:
        prompt_text = out.get("master_research_prompt", "")
        c1, c2, c3 = st.columns(3)
        c1.metric("Prompt length", f"{len(prompt_text):,} chars")
        c2.metric("Question groups", len(out.get("research_questions", [])))
        c3.metric("Est. Perplexity cost", f"${out.get('estimated_perplexity_cost_usd', 0):.2f}")

        st.markdown('<div class="section-label">Master Research Prompt — copy into Perplexity Pro</div>', unsafe_allow_html=True)
        st.code(prompt_text, language="markdown")
        st.download_button(
            "💾 Download as .txt",
            prompt_text,
            file_name=f"research_prompt_{run['topic'].replace(' ','_')}.txt",
        )

        if out.get("source_priority"):
            st.markdown('<div class="section-label">Source Priority</div>', unsafe_allow_html=True)
            for s in out["source_priority"]:
                st.caption(f"• {s}")
    render_agent_section("research_prompt_generator", 6)


# ── Tab 7: All Artifacts ──────────────────────────────────────────────────
with agent_tabs[7]:
    st.markdown("### All Artifacts for this Run")
    state = m["get_full_run_state"](view_run_id)
    if state:
        for agent_name, output in state["outputs"].items():
            if not output:
                continue
            # FIX: meta can be None if metadata.json wasn't saved.
            meta = state["metadata"].get(agent_name) or {}
            cost = meta.get("cost_usd", 0) or 0
            llm_calls = meta.get("llm_calls", 0) or 0
            serp_calls = meta.get("serp_calls", 0) or 0
            validation = "✓" if meta.get("validation_passed") else "?"
            with st.expander(f"**{agent_name}** · ${cost:.4f} · {llm_calls} LLM · {serp_calls} SERP · {validation}"):
                if meta.get("validation_problems"):
                    st.warning("Validation: " + ", ".join(meta["validation_problems"][:5]))
                st.json(output)