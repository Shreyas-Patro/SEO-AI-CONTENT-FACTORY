"""
Canvas Homes — Main dashboard entry point.

Run with:  streamlit run dashboard/app.py
"""
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Canvas Homes · AI Pipeline",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

from dashboard.styles import inject_styles
from dashboard.auth import require_login, logout_button, is_admin

inject_styles()


# ── Session state init ──
def _init_state():
    defaults = {
        "topic": "HSR Layout",
        "current_run_id": None,
        "viewing_run_id": None,
        "layer1_thread": None,
        "layer2_thread": None,
        "layer3_thread": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


# ── Auth check ──
require_login()


# ── Module loading ──
@st.cache_resource(show_spinner=False)
def _load_modules():
    from orchestrator import (
        start_pipeline_run, run_layer1, run_layer2, run_layer3,
        approve_gate, reject_gate, rerun_agent,
        load_agent_output, load_agent_input, load_agent_metadata,
        load_agent_console, edit_agent_output, edit_state_key,
    )
    from db.artifacts import (
        list_pipeline_runs, get_pipeline_run, list_artifacts,
        get_artifact_path, load_state,
    )
    return {
        "start_pipeline_run": start_pipeline_run,
        "run_layer1": run_layer1,
        "run_layer2": run_layer2,
        "run_layer3": run_layer3,
        "approve_gate": approve_gate,
        "reject_gate": reject_gate,
        "rerun_agent": rerun_agent,
        "load_agent_output": load_agent_output,
        "load_agent_input": load_agent_input,
        "load_agent_metadata": load_agent_metadata,
        "load_agent_console": load_agent_console,
        "edit_agent_output": edit_agent_output,
        "edit_state_key": edit_state_key,
        "list_pipeline_runs": list_pipeline_runs,
        "get_pipeline_run": get_pipeline_run,
        "list_artifacts": list_artifacts,
        "get_artifact_path": get_artifact_path,
        "load_state": load_state,
    }


m = _load_modules()


# ── Sidebar ──
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.caption("AI Pipeline v2")

    topic = st.text_input("Topic", value=st.session_state.topic, key="topic_in")
    st.session_state.topic = topic

    if st.button("▶ Start New Run", type="primary"):
        run_id = m["start_pipeline_run"](topic)
        st.session_state.current_run_id = run_id
        st.session_state.viewing_run_id = run_id
        for k in ("layer1_thread", "layer2_thread", "layer3_thread"):
            st.session_state[k] = None
        st.rerun()

    st.markdown("---")
    runs = m["list_pipeline_runs"](limit=20)
    if runs:
        labels = [f"{r['id'][-8:]} · {r['topic'][:18]} · {r['status']}" for r in runs]
        idx = st.selectbox(
            "Past runs",
            range(len(runs)),
            format_func=lambda i: labels[i],
            label_visibility="collapsed",
        )
        if st.button("📂 Open"):
            st.session_state.viewing_run_id = runs[idx]["id"]
            st.session_state.current_run_id = runs[idx]["id"]
            st.rerun()

    st.markdown("---")
    if st.session_state.current_run_id:
        run = m["get_pipeline_run"](st.session_state.current_run_id)
        if run:
            st.caption(f"Run: `{run['id'][-8:]}`")
            st.markdown(f"**{run['status']}** · {run.get('current_stage') or 'init'}")
            st.markdown(f"💰 ${run.get('total_cost_usd',0) or 0:.4f}")
            st.markdown(
                f"🔍 {run.get('total_serp_calls',0) or 0} SERP · "
                f"🤖 {run.get('total_llm_calls',0) or 0} LLM"
            )

    st.markdown("---")
    logout_button()


# ── Tabs ──
tab_names = [
    "🚀 Pipeline",
    "🎯 Topic Queue",
    "📝 Articles",
    "❓ FAQs",
    "🔗 Interlinking",
    "📥 Ingestion",
    "🕸️ Graph",
    "🗂️ Artifacts",
]
if is_admin():
    tab_names.append("🔐 Admin")

tabs = st.tabs(tab_names)

with tabs[0]:
    from dashboard.views import pipeline
    pipeline.render(m)

with tabs[1]:
    from dashboard.views import queue
    queue.render(m)

with tabs[2]:
    from dashboard.views import articles
    articles.render(m)

with tabs[3]:
    from dashboard.views import faqs
    faqs.render(m)

with tabs[4]:
    from dashboard.views import interlinking
    interlinking.render(m)

with tabs[5]:
    try:
        from dashboard_components.ingestion_view import render_ingestion_view
        render_ingestion_view(st.session_state.viewing_run_id)
    except Exception as e:
        st.error(f"Ingestion view error: {e}")

with tabs[6]:
    try:
        from dashboard_components.graph_view import render_graph_view
        render_graph_view()
    except Exception as e:
        st.error(f"Graph view error: {e}")

with tabs[7]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
    else:
        st.markdown(f"### Artifacts for `{rid}`")
        artifacts = m["list_artifacts"](rid)
        for agent_name, files in artifacts.items():
            with st.expander(f"📁 {agent_name} ({len(files)} files)"):
                for f in files:
                    st.caption(f"`{f['kind']}` · {f['byte_size']} bytes · `{f['file_path']}`")

if is_admin():
    with tabs[-1]:
        from dashboard.views import admin
        admin.render()