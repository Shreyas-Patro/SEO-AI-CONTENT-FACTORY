"""
Canvas Homes — Pipeline Dashboard v6 (LangGraph-aware)

Tabs:
  🚀 Pipeline   — run controls + per-agent status + live log
  📝 Articles   — article queue, content viewer, history, quality scores
  🔄 Quality    — per-article quality loop visualization
  🔗 Interlink  — cluster + global interlinking
  📊 Opportunity, 📥 Ingestion, 🕸️ Graph, 🗂️ Artifacts (kept from v5)
"""
import os
import sys
import time
import json
import threading
import contextlib
from pathlib import Path
from datetime import datetime

import streamlit as st

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Canvas Homes · AI Pipeline v6",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auto-refresh every 2s while a run is active ─────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    HAVE_AUTOREFRESH = True
except ImportError:
    HAVE_AUTOREFRESH = False

# ── STYLES ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
.block-container { padding: 1rem 2rem 2rem; max-width: 1600px; }
section[data-testid="stSidebar"] { background: #0a0a14; border-right: 1px solid #1a1a2e; }
section[data-testid="stSidebar"] .stButton button {
  background: linear-gradient(90deg, #c8ff00, #9eff00);
  color: #000 !important; font-weight: 700; border: none;
  border-radius: 8px; width: 100%; font-size: 13px; transition: all .15s;
}
section[data-testid="stSidebar"] .stButton button:hover { transform: translateY(-1px); }
[data-testid="metric-container"] {
  background: linear-gradient(135deg, #161624, #1a1a2e);
  border: 1px solid #2a2a4a; border-radius: 12px; padding: 14px 16px;
}
.agent-card {
  border-radius: 10px; padding: 12px 14px; margin: 4px 0;
  min-height: 76px; transition: all .2s;
}
.agent-pending { background: #161624; border: 1px solid #1f1f35; opacity: .55; }
.agent-active {
  background: linear-gradient(120deg, #1f3a1f, #0a1f0a);
  border: 2px solid #00ff88; box-shadow: 0 0 16px rgba(0,255,136,.35);
  animation: glow 2s ease-in-out infinite;
}
.agent-done   { background: #0a1a2a; border: 1px solid #1f3a5a; }
.agent-failed { background: #2a0a0a; border: 1px solid #5a1f1f; }
.agent-name   { font-weight: 700; font-size: 12px; color: #fff; }
.agent-detail { font-family: ui-monospace, monospace; font-size: 10px; color: #aaa; margin-top: 4px; }
.pulse {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: #00ff88; box-shadow: 0 0 8px #00ff88;
  animation: pulse 1.4s ease-in-out infinite; margin-right: 6px;
}
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.4)} }
@keyframes glow { 0%,100%{box-shadow: 0 0 12px rgba(0,255,136,.35)} 50%{box-shadow: 0 0 24px rgba(0,255,136,.6)} }
.score-pill {
  display: inline-block; padding: 3px 10px; border-radius: 12px;
  font-size: 11px; font-weight: 600; margin-right: 6px;
}
.score-good { background: #1a4a1a; color: #9eff9e; }
.score-warn { background: #4a3a1a; color: #ffcc66; }
.score-bad  { background: #4a1a1a; color: #ff8888; }
.iter-badge {
  background: #2a1a4a; color: #c8a8ff;
  padding: 2px 8px; border-radius: 8px; font-size: 10px; font-weight: 600;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ───────────────────────────────────────────────────────
def _init():
    defaults = {
        "topic": "Hosa Road",
        "current_run_id": None,
        "viewing_run_id": None,
        "layer1_thread": None,
        "layer2_thread": None,
        "layer3_thread": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init()


# ── Tee logging (captures stdout from the bg thread to a file) ──────────
class _Tee:
    def __init__(self, log_file, original):
        self.log_file = log_file
        self.original = original
    def write(self, s):
        try: self.original.write(s)
        except Exception: pass
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(s); f.flush()
        except Exception: pass
        return len(s) if s else 0
    def flush(self):
        try: self.original.flush()
        except Exception: pass

@contextlib.contextmanager
def tee_to_file(log_file):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).write_text("", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(log_file, old_out)
    sys.stderr = _Tee(log_file, old_err)
    try: yield
    finally: sys.stdout, sys.stderr = old_out, old_err


def run_in_bg(fn, run_id, log_file, err_holder, *args):
    def _target():
        try:
            with tee_to_file(log_file):
                fn(run_id, *args)
        except Exception as e:
            err_holder["error"] = f"{type(e).__name__}: {e}"
            try:
                with open(log_file, "a") as f:
                    import traceback
                    f.write(f"\n❌ {type(e).__name__}: {e}\n{traceback.format_exc()}")
            except Exception: pass
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


# ── Load orchestrator + helpers ─────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _load():
    try:
        from orchestrator import (
            start_pipeline_run, run_layer1, run_layer2, run_layer3,
            approve_gate, reject_gate, rerun_agent,
            load_agent_output, load_agent_input, load_agent_metadata,
            load_agent_console, edit_agent_output, edit_state_key,
            get_full_run_state,
        )
        from db.artifacts import (
            list_pipeline_runs, get_pipeline_run, list_artifacts,
            load_state,
        )
        from db.pipeline_state import StateKeys
        return locals()
    except Exception as e:
        return {"error": str(e)}

m = _load()
if "error" in m:
    st.error(f"Module load error: {m['error']}")
    st.stop()


# ── Helpers ─────────────────────────────────────────────────────────────
def is_alive(t):
    return t is not None and t.is_alive()

def any_running():
    return any(is_alive(st.session_state.get(f"layer{i}_thread")) for i in (1, 2, 3))

def get_agent_status(run_id, agent_name):
    base = Path("runs") / run_id / agent_name
    if not base.exists(): return "pending"
    out = base / "output.json"
    meta = base / "metadata.json"
    if out.exists():
        if meta.exists():
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                if d.get("status") == "failed": return "failed"
            except Exception: pass
        return "done"
    if (base / "input.json").exists(): return "active"
    return "pending"

# Lift dashboard_components into views
from dashboard_components.pipeline_view import render_pipeline_view
from dashboard_components.article_review import render_article_review
from dashboard_components.quality_view import render_quality_view
from dashboard_components.interlinking_view import render_interlinking_view


# ── SIDEBAR ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.caption("AI PIPELINE v6 · LangGraph")

    topic = st.text_input("Topic", value=st.session_state.topic, key="topic_in")
    st.session_state.topic = topic

    if st.button("▶ Start New Run", type="primary"):
        run_id = m["start_pipeline_run"](topic)
        st.session_state.current_run_id = run_id
        st.session_state.viewing_run_id = run_id
        for k in ("layer1_thread","layer2_thread","layer3_thread"):
            st.session_state[k] = None
        st.rerun()

    st.markdown("---")
    runs = m["list_pipeline_runs"](limit=20)
    if runs:
        labels = [f"{r['id'][-8:]} · {r['topic'][:18]} · {r['status']}" for r in runs]
        idx = st.selectbox("Past runs", range(len(runs)),
                           format_func=lambda i: labels[i],
                           label_visibility="collapsed")
        if st.button("📂 Open"):
            st.session_state.viewing_run_id = runs[idx]["id"]
            st.session_state.current_run_id = runs[idx]["id"]
            st.rerun()

    st.markdown("---")
    if st.session_state.current_run_id:
        run = m["get_pipeline_run"](st.session_state.current_run_id)
        if run:
            st.caption(f"Run: `{run['id'][-8:]}`")
            st.markdown(f"**{run['status']}** · `{run.get('current_stage') or 'init'}`")
            c1, c2 = st.columns(2)
            c1.metric("Cost", f"${run.get('total_cost_usd',0) or 0:.4f}")
            c2.metric("LLM", run.get('total_llm_calls',0) or 0)
            st.caption(f"🔍 {run.get('total_serp_calls',0) or 0} SERP calls")


# ── Auto-refresh while running ──────────────────────────────────────────
if any_running() and HAVE_AUTOREFRESH:
    st_autorefresh(interval=2000, key="auto_refresh")


# ── MAIN LAYOUT ─────────────────────────────────────────────────────────
st.markdown("# 🏠 Canvas Homes AI Pipeline")

tabs = st.tabs([
    "🚀 Pipeline", "📝 Articles", "🔄 Quality",
    "🔗 Interlink", "📊 Opportunity", "📥 Ingestion",
    "🕸️ Graph", "🗂️ Artifacts",
])

with tabs[0]:
    render_pipeline_view(m, run_in_bg, is_alive)

with tabs[1]:
    render_article_review(m)

with tabs[2]:
    render_quality_view(m)

with tabs[3]:
    render_interlinking_view(m)

with tabs[4]:
    try:
        from dashboard_components.opportunity_view import render_opportunity_view
        render_opportunity_view(st.session_state.viewing_run_id, m)
    except Exception as e:
        st.error(f"Opportunity view error: {e}")

with tabs[5]:
    try:
        from dashboard_components.ingestion_view import render_ingestion_view
        render_ingestion_view(st.session_state.viewing_run_id)
    except Exception as e:
        st.error(f"Ingestion error: {e}")

with tabs[6]:
    try:
        from dashboard_components.graph_view import render_graph_view
        render_graph_view()
    except Exception as e:
        st.error(f"Graph error: {e}")

with tabs[7]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
    else:
        artifacts = m["list_artifacts"](rid)
        st.markdown(f"### Artifacts for `{rid}`")
        for agent_name, files in artifacts.items():
            with st.expander(f"📁 {agent_name} ({len(files)} files)"):
                for f in files:
                    st.caption(f"`{f['kind']}` · {f['byte_size']} bytes · `{f['file_path']}`")