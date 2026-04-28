"""
Canvas Homes — AI Agent Pipeline Dashboard (v3)

NEW IN V3:
- Opportunity Matrix tab (2-axis scatter — articles, FAQs, combined)
- Research Ingestion tab (upload .md/.pdf/.docx → fact extraction)
- Knowledge Graph tab
- Console log per agent (you can read what each agent printed)
- Edit any agent's output AND any PipelineState key
- File-system artifacts (everything in runs/<run_id>/<agent>/{input,output,metadata}.json)
- Per-agent SERP/LLM call counts visible (including cache hits)

Run from project root:
    streamlit run dashboard.py
"""

import streamlit as st
import sys
import os
import json
import io
import contextlib
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Canvas Homes · AI Pipeline v3",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── STYLES (same compact dark theme as before) ─────────────────────────────
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
.console-box {
    background: #050508; border: 1px solid #1f1f35; border-radius: 8px;
    padding: 14px 16px; font-family: 'Space Mono', monospace; font-size: 11px;
    line-height: 1.8; color: #8888aa; max-height: 320px; overflow-y: auto;
    white-space: pre-wrap; word-break: break-all;
}
.section-label {
    font-family: 'Space Mono', monospace; font-size: 9px; color: #8888aa;
    letter-spacing: 0.15em; text-transform: uppercase;
    border-bottom: 1px solid #1f1f35; padding-bottom: 6px; margin-bottom: 12px;
}
.data-card {
    background: #161624; border: 1px solid #1f1f35; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 8px;
}
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
        cls_color = {"info": "#5a9fff", "success": "#00ff88",
                     "warn": "#ffcc00", "error": "#ff6b6b"}.get(kind, "#8888aa")
        safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += f'<span style="color:#444">[{ts}]</span> <span style="color:{cls_color}">{safe}</span>\n'
    html += "</div>"
    return html


class _LogCapture(io.StringIO):
    def write(self, s):
        super().write(s)
        s = s.strip()
        if not s:
            return
        kind = "info"
        if any(x in s for x in ["✅", "complete", "Complete"]): kind = "success"
        elif any(x in s for x in ["❌", "Error", "error", "Failed"]): kind = "error"
        elif any(x in s for x in ["⚠️", "Warning"]): kind = "warn"
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
            load_agent_console, edit_agent_output, edit_state_key,
            get_full_run_state,
        )
        from db.artifacts import (
            list_pipeline_runs, get_pipeline_run, list_artifacts,
            get_artifact_path, load_state,
        )
        from db.pipeline_state import StateKeys
        from db.sqlite_ops import get_stats, list_clusters, get_articles_by_cluster

        return {
            "start_pipeline_run": start_pipeline_run,
            "run_layer1": run_layer1, "run_layer2": run_layer2,
            "approve_gate": approve_gate, "reject_gate": reject_gate,
            "load_agent_output": load_agent_output, "load_agent_input": load_agent_input,
            "load_agent_metadata": load_agent_metadata,
            "load_agent_console": load_agent_console,
            "edit_agent_output": edit_agent_output,
            "edit_state_key": edit_state_key,
            "get_full_run_state": get_full_run_state,
            "list_pipeline_runs": list_pipeline_runs,
            "get_pipeline_run": get_pipeline_run,
            "list_artifacts": list_artifacts,
            "get_artifact_path": get_artifact_path,
            "load_state": load_state,
            "StateKeys": StateKeys,
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
    st.caption("AI AGENT PIPELINE v3")

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
    st.markdown("**Past Runs**")
    runs = m["list_pipeline_runs"](limit=20)
    if not runs:
        st.caption("No runs yet")
    else:
        run_labels = [f"{r['id'][-8:]} · {r['topic'][:18]} · {r['status']}" for r in runs]
        idx = st.selectbox(
            "View previous run", range(len(runs)),
            format_func=lambda i: run_labels[i], label_visibility="collapsed",
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
            st.caption(f"📁 `{run.get('artifact_path','')}`")


# ══════════════════════════════════════════════════════════════════════════
# MAIN TABS
# ══════════════════════════════════════════════════════════════════════════
st.markdown("# AI Agent Pipeline")

main_tabs = st.tabs([
    "🚀 Pipeline", "📊 Opportunity Matrix",
    "📥 Ingestion", "🕸️ Graph",
    "🗂️ Artifacts Browser",
])

# ─── Tab 1: Pipeline (the original view, slimmed) ─────────────────────────
with main_tabs[0]:
    view_run_id = st.session_state.viewing_run_id

    if not view_run_id:
        st.info("Start a new pipeline run from the sidebar, or open a past run.")
        st.stop()

    run = m["get_pipeline_run"](view_run_id)
    if not run:
        st.error("Run not found")
        st.stop()

    col1, col2, col3 = st.columns([2, 1, 1])
    col1.markdown(f"**Topic:** `{run['topic']}`  ·  **Run:** `{view_run_id}`")

    stage = run.get("current_stage", "init")
    gate = run.get("gate_status", "pending")
    status = run.get("status", "running")

    with col2:
        if status == "running" and stage in ("init", "_pipeline"):
            if st.button("▶ Run Layer 1", type="primary"):
                with st.spinner("Running Layer 1..."):
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
                with st.spinner("Running Layer 2..."):
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
            if st.button("✅ Approve Gate", type="primary", key="gate_approve_main"):
                m["approve_gate"](view_run_id)
                clog("Gate approved", "success")
                st.rerun()

    # Console
    with st.expander("🖥️ Live Console (this session)", expanded=False):
        st.markdown(render_console(), unsafe_allow_html=True)

    st.markdown("---")

    # ── Per-agent tabs ────────────────────────────────────────────────────
    agent_tabs = st.tabs([
        "📡 Trend Scout", "🕵️ Competitor Spy", "🗺️ Keyword Mapper",
        "🚪 Gate", "🏗️ Content Architect", "❓ FAQ Architect",
        "🔬 Research Prompt",
    ])

    def render_agent_section(agent_key):
        inp = m["load_agent_input"](view_run_id, agent_key)
        out = m["load_agent_output"](view_run_id, agent_key)
        meta = m["load_agent_metadata"](view_run_id, agent_key)
        console = m["load_agent_console"](view_run_id, agent_key)

        if not out and not inp:
            st.info(f"{agent_key} has not run yet.")
            return

        if meta and isinstance(meta, dict):
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Cost", f"${meta.get('cost_usd', 0):.4f}")
            c2.metric("LLM calls", meta.get("llm_calls", 0))
            c3.metric("LLM cache", meta.get("llm_cache_hits", 0))
            c4.metric("SERP calls", meta.get("serp_calls", 0))
            c5.metric("SERP cache", meta.get("serp_cache_hits", 0))

            if meta.get("validation_problems"):
                with st.expander("⚠️ Validation problems"):
                    for p in meta["validation_problems"]:
                        st.warning(p)

        # Show file paths so user can find them on disk
        if out is not None:
            file_path = m["get_artifact_path"](view_run_id, agent_key, "output")
            st.caption(f"📁 Output file: `{file_path}`")

        with st.expander("📥 Input"):
            st.json(inp) if inp else st.caption("No input artifact")

        with st.expander("📤 Output", expanded=True):
            edit_key = f"edit_{agent_key}_{view_run_id}"
            if st.button("✏️ Edit output", key=f"btn_{edit_key}"):
                st.session_state[edit_key] = True

            if st.session_state.get(edit_key):
                edited = st.text_area(
                    "Edit JSON:", value=json.dumps(out, indent=2),
                    height=400, key=f"ta_{edit_key}",
                )
                col_save, col_cancel = st.columns(2)
                if col_save.button("💾 Save", key=f"save_{edit_key}"):
                    try:
                        new_data = json.loads(edited)
                        m["edit_agent_output"](view_run_id, agent_key, new_data)
                        st.session_state[edit_key] = False
                        st.success("Saved.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
                if col_cancel.button("Cancel", key=f"cancel_{edit_key}"):
                    st.session_state[edit_key] = False
                    st.rerun()
            else:
                st.json(out) if out else st.caption("No output yet")

        if console:
            with st.expander("🖥️ Console log (what this agent printed)"):
                st.code(console[-5000:], language=None)

    with agent_tabs[0]:
        render_agent_section("trend_scout")
    with agent_tabs[1]:
        render_agent_section("competitor_spy")
    with agent_tabs[2]:
        render_agent_section("keyword_mapper")
    with agent_tabs[3]:
        st.markdown("### Human Approval Gate")
        if gate == "pending" and stage == "gate_pending":
            st.warning("Layer 1 complete. Review the outputs above, then approve.")
            c1, c2 = st.columns(2)
            if c1.button("✅ Approve", type="primary", key="gate_a2"):
                m["approve_gate"](view_run_id); st.rerun()
            if c2.button("✗ Reject", key="gate_r2"):
                m["reject_gate"](view_run_id); st.rerun()
        elif gate == "approved":
            st.success("Gate approved — Layer 2 ready.")
        elif gate == "rejected":
            st.error("Gate rejected.")
        else:
            st.info("Run Layer 1 first.")
    with agent_tabs[4]:
        out = m["load_agent_output"](view_run_id, "content_architect")
        if out:
            plan = out.get("cluster_plan", {})
            articles = plan.get("articles", [])
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total articles", len(articles))
            c2.metric("Hubs", out.get("hub_count", 0))
            c3.metric("Spokes", out.get("spoke_count", 0))
            c4.metric("Sub-spokes", out.get("sub_spoke_count", 0))
            if out.get("n_keyword_groups"):
                st.caption(f"Keyword groups: {out['n_keyword_groups']} → Articles: {out['n_articles']}")

            for art in articles:
                with st.expander(f"**[{art.get('type','?').upper()}]** {art.get('title','—')}"):
                    st.markdown(f"**Slug:** `/{art.get('slug','—')}`")
                    brief = art.get("writer_brief", {})
                    if brief:
                        st.markdown(f"**Angle:** {brief.get('angle','—')}")
                        st.markdown(f"**Target reader:** {brief.get('target_reader','—')}")
                        st.markdown("**Must answer:**")
                        for q in brief.get("must_answer", []):
                            st.markdown(f"- {q}")
                        st.markdown("**Outline:**")
                        for h in brief.get("outline", []):
                            level = h.get("level", "H2") if isinstance(h, dict) else "H2"
                            heading = h.get("heading", str(h)) if isinstance(h, dict) else str(h)
                            covers = h.get("covers", "") if isinstance(h, dict) else ""
                            st.markdown(f"- **{level}** {heading}" + (f" — _{covers}_" if covers else ""))
        render_agent_section("content_architect")
    with agent_tabs[5]:
        out = m["load_agent_output"](view_run_id, "faq_architect")
        if out:
            c1, c2, c3 = st.columns(3)
            c1.metric("Input questions", out.get("total_input_questions", 0))
            c2.metric("Kept (deduplicated)", out.get("kept_questions", 0))
            c3.metric("Dropped", out.get("dropped_count", 0))
            allocation = out.get("allocation_by_article", {})
            for slug, faqs in allocation.items():
                with st.expander(f"📄 `{slug}` — {len(faqs)} FAQs"):
                    for f in faqs:
                        st.markdown(f"**Q:** {f.get('question','—')}")
                        st.caption(
                            f"Intent: {f.get('intent','—')} | "
                            f"AEO: {f.get('aeo_value','—')} | "
                            f"Length: {f.get('expected_answer_length','—')}"
                        )
        render_agent_section("faq_architect")
    with agent_tabs[6]:
        out = m["load_agent_output"](view_run_id, "research_prompt_generator")
        if out:
            prompt_text = out.get("master_research_prompt", "")
            c1, c2 = st.columns(2)
            c1.metric("Prompt length", f"{len(prompt_text):,} chars")
            c2.metric("Question groups", len(out.get("research_questions", [])))
            st.code(prompt_text, language="markdown")
            st.download_button(
                "💾 Download as .txt", prompt_text,
                file_name=f"research_prompt_{run['topic'].replace(' ','_')}.txt",
            )
        render_agent_section("research_prompt_generator")


# ─── Tab 2: Opportunity Matrix ────────────────────────────────────────────
with main_tabs[1]:
    if not st.session_state.viewing_run_id:
        st.info("Open a pipeline run first.")
    else:
        from dashboard_components.opportunity_view import render_opportunity_view
        render_opportunity_view(st.session_state.viewing_run_id, m)


# ─── Tab 3: Ingestion ─────────────────────────────────────────────────────
with main_tabs[2]:
    from dashboard_components.ingestion_view import render_ingestion_view
    render_ingestion_view(st.session_state.viewing_run_id)


# ─── Tab 4: Graph ─────────────────────────────────────────────────────────
with main_tabs[3]:
    from dashboard_components.graph_view import render_graph_view
    render_graph_view()


# ─── Tab 5: Artifacts Browser ─────────────────────────────────────────────
with main_tabs[4]:
    if not st.session_state.viewing_run_id:
        st.info("Open a run first.")
    else:
        view_run_id = st.session_state.viewing_run_id
        run = m["get_pipeline_run"](view_run_id)
        st.markdown(f"### Artifacts for `{view_run_id}`")
        st.caption(f"Disk path: `{run.get('artifact_path','')}`")

        # PipelineState editor
        with st.expander("🧠 Edit PipelineState (shared memory)"):
            state = m["load_state"](view_run_id)
            st.markdown(f"**Stage:** `{state.get('stage','?')}`")
            st.markdown(f"**Agents completed:** {state.get('agents_completed', [])}")
            shared_keys = list(state.get("shared", {}).keys())
            if shared_keys:
                key_to_edit = st.selectbox("State key to edit", shared_keys)
                edited = st.text_area(
                    "JSON value:",
                    value=json.dumps(state["shared"][key_to_edit], indent=2),
                    height=300, key=f"state_edit_{key_to_edit}",
                )
                if st.button("💾 Save state edit"):
                    try:
                        new_val = json.loads(edited)
                        m["edit_state_key"](view_run_id, key_to_edit, new_val)
                        st.success("State updated.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")

        # Artifact index
        artifacts = m["list_artifacts"](view_run_id)
        for agent_name, files in artifacts.items():
            with st.expander(f"📁 {agent_name} ({len(files)} files)"):
                for f in files:
                    st.markdown(
                        f"`{f['kind']}` · {f['byte_size']} bytes · "
                        f"`{f['file_path']}`"
                    )
