"""
Canvas Homes — AI Agent Pipeline Dashboard (v3.4)

FIXES IN THIS VERSION:
- Action buttons are ALWAYS VISIBLE (greyed out when not applicable, with
  reason). No more hidden Run Layer 1 button due to NULL stage.
- Live agent status panel reads runs/<run_id>/<agent>/ every 2s while a
  layer is executing, so you see real-time progress.
- Live console streams the agent's stdout to runs/<run_id>/_live.log as
  it runs, not after.
- Background thread runs the layer so the UI stays responsive.

Run from project root:
    streamlit run dashboard.py
"""

import streamlit as st
import sys
import os
import json
import time
import contextlib
import threading
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

# ── STYLES ─────────────────────────────────────────────────────────────────
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
.status-box {
    background: #0a1f0a; border: 1px solid #1a4a1a; border-radius: 10px;
    padding: 12px 14px; margin: 4px 0; min-height: 70px;
}
.status-active {
    background: linear-gradient(90deg, #1a3a1a 0%, #0a1f0a 100%);
    border: 2px solid #00ff88; box-shadow: 0 0 12px rgba(0,255,136,0.3);
}
.status-pending {
    background: #161624; border: 1px solid #1f1f35; opacity: 0.5;
}
.status-done {
    background: #0a1a2a; border: 1px solid #1f3a5a;
}
.status-failed {
    background: #2a0a0a; border: 1px solid #5a1f1f;
}
.agent-name { font-weight: 700; font-size: 12px; color: #fff; }
.agent-detail { font-family: 'Space Mono', monospace; font-size: 10px; color: #aaa; margin-top: 4px; }
.pulse {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #00ff88; box-shadow: 0 0 6px #00ff88;
    animation: pulse 1.4s ease-in-out infinite; margin-right: 6px;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.5; transform: scale(1.3); }
}
</style>
""", unsafe_allow_html=True)


# ── SESSION STATE ──────────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "topic": "Hosa Road",
        "current_run_id": None,
        "viewing_run_id": None,
        "layer1_thread": None,
        "layer2_thread": None,
        "thread_error": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init_state()


# ── TEE: write stdout to BOTH the screen AND a file the dashboard polls ──
class _TeeToFile:
    def __init__(self, log_file, original):
        self.log_file = log_file
        self.original = original

    def write(self, s):
        try:
            self.original.write(s)
        except Exception:
            pass
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(s)
                f.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass


@contextlib.contextmanager
def tee_to_file(log_file):
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    Path(log_file).write_text("", encoding="utf-8")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = _TeeToFile(log_file, old_out)
    sys.stderr = _TeeToFile(log_file, old_err)
    try:
        yield
    finally:
        sys.stdout = old_out
        sys.stderr = old_err


# ── BACKGROUND RUNNER ──────────────────────────────────────────────────────
def run_layer_in_background(layer_fn, run_id, log_file, error_holder):
    """Run a layer function in a daemon thread with stdout teed to log_file."""
    def _target():
        try:
            with tee_to_file(log_file):
                layer_fn(run_id)
        except Exception as e:
            error_holder["error"] = f"{type(e).__name__}: {e}"
            try:
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"\n\n❌ FATAL: {type(e).__name__}: {e}\n")
                    import traceback
                    f.write(traceback.format_exc())
            except Exception:
                pass
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


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
        }
    except Exception as e:
        return {"error": str(e)}

m = _load()
if "error" in m:
    st.error(f"Module load error: {m['error']}")
    st.stop()


# ── HEARTBEAT POLLING ──────────────────────────────────────────────────────
AGENT_ORDER = [
    ("trend_scout",            "📡 Trend Scout"),
    ("competitor_spy",         "🕵️ Comp. Spy"),
    ("keyword_mapper",         "🗺️ Kw Mapper"),
    ("content_architect",      "🏗️ Content"),
    ("faq_architect",          "❓ FAQ"),
    ("research_prompt_generator", "🔬 Research"),
]


def get_agent_status(run_id, agent_name):
    """Determine status: 'pending' | 'active' | 'done' | 'failed'."""
    base = Path("runs") / run_id / agent_name
    if not base.exists():
        return "pending"
    out = base / "output.json"
    meta = base / "metadata.json"
    if out.exists():
        if meta.exists():
            try:
                m_data = json.loads(meta.read_text(encoding="utf-8"))
                if m_data.get("status") == "failed":
                    return "failed"
            except Exception:
                pass
        return "done"
    return "active"


def is_thread_alive(t):
    return t is not None and t.is_alive()


def render_live_agent_panel(run_id):
    """Show every agent's current status with a pulse on the active one."""
    layer1_active = is_thread_alive(st.session_state.layer1_thread)
    layer2_active = is_thread_alive(st.session_state.layer2_thread)
    is_active = layer1_active or layer2_active

    st.markdown("##### Agent Status")

    cols = st.columns(len(AGENT_ORDER))
    for i, (key, label) in enumerate(AGENT_ORDER):
        status = get_agent_status(run_id, key)
        with cols[i]:
            if status == "active":
                st.markdown(
                    f'<div class="status-box status-active">'
                    f'<div class="agent-name">{label}</div>'
                    f'<div class="agent-detail"><span class="pulse"></span>running…</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif status == "done":
                meta = m["load_agent_metadata"](run_id, key) or {}
                cost = meta.get("cost_usd", 0) or 0
                serp = meta.get("serp_calls", 0) or 0
                llm = meta.get("llm_calls", 0) or 0
                st.markdown(
                    f'<div class="status-box status-done">'
                    f'<div class="agent-name">{label} ✓</div>'
                    f'<div class="agent-detail">${cost:.4f}<br>{serp}🔍 · {llm}🤖</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif status == "failed":
                st.markdown(
                    f'<div class="status-box status-failed">'
                    f'<div class="agent-name">{label} ✗</div>'
                    f'<div class="agent-detail">failed</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="status-box status-pending">'
                    f'<div class="agent-name">{label}</div>'
                    f'<div class="agent-detail">waiting</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    return is_active


def render_live_log(run_id):
    """Show the live tail of the captured log file for the current run."""
    log_file = Path("runs") / run_id / "_live.log"
    if not log_file.exists():
        st.caption("(waiting for log…)")
        return
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    tail = text[-6000:] if len(text) > 6000 else text
    st.markdown("##### Live Console")
    st.code(tail or "(no output yet)", language=None)


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.caption("AI AGENT PIPELINE v3.4")

    st.markdown("**New Pipeline Run**")
    topic = st.text_input("Topic", value=st.session_state.topic, key="topic_input")
    st.session_state.topic = topic

    if st.button("▶ Start New Run", type="primary"):
        run_id = m["start_pipeline_run"](topic)
        st.session_state.current_run_id = run_id
        st.session_state.viewing_run_id = run_id
        st.session_state.layer1_thread = None
        st.session_state.layer2_thread = None
        st.session_state.thread_error = None
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
            st.session_state.layer1_thread = None
            st.session_state.layer2_thread = None
            st.rerun()

    st.markdown("---")
    if st.session_state.current_run_id:
        run = m["get_pipeline_run"](st.session_state.current_run_id)
        if run:
            st.caption(f"Current: `{run['id'][-8:]}`")
            st.markdown(f"**Status:** {run['status']}")
            st.markdown(f"**Stage:** {run.get('current_stage') or 'init'}")
            st.markdown(f"**Cost:** ${run.get('total_cost_usd',0) or 0:.4f}")
            st.markdown(f"**SERP calls:** {run.get('total_serp_calls',0) or 0}")
            st.markdown(f"**LLM calls:** {run.get('total_llm_calls',0) or 0}")
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

with main_tabs[0]:
    view_run_id = st.session_state.viewing_run_id

    if not view_run_id:
        st.info("Start a new pipeline run from the sidebar, or open a past run.")
        st.stop()

    run = m["get_pipeline_run"](view_run_id)
    if not run:
        st.error("Run not found")
        st.stop()

    # Normalize stage — treat None and empty as 'init'
    stage = run.get("current_stage") or "init"
    gate = run.get("gate_status") or "pending"
    status = run.get("status") or "running"

    st.markdown(f"**Topic:** `{run['topic']}`  ·  **Run:** `{view_run_id}`  ·  **Stage:** `{stage}`")

    # ── ALWAYS VISIBLE ACTION BAR ──────────────────────────────────────────
    layer1_running = is_thread_alive(st.session_state.layer1_thread)
    layer2_running = is_thread_alive(st.session_state.layer2_thread)

    bcol1, bcol2, bcol3, bcol4 = st.columns([1, 1, 1, 1])

    # ── Run Layer 1
    with bcol1:
        layer1_done_stages = (
            "gate_pending", "gate_approved",
            "layer2_content_architect", "layer2_faq_architect",
            "layer2_research_prompt_gen", "done",
        )
        layer1_done = stage in layer1_done_stages
        l1_disabled = layer1_running or layer1_done or status == "completed"
        l1_label = "▶ Run Layer 1"
        if layer1_running: l1_label = "⏳ Layer 1 running…"
        elif layer1_done: l1_label = "✓ Layer 1 done"
        if st.button(l1_label, type="primary", disabled=l1_disabled,
                     key=f"l1_{view_run_id}", use_container_width=True):
            log_file = Path("runs") / view_run_id / "_live.log"
            err_holder = {"error": None}
            t = run_layer_in_background(m["run_layer1"], view_run_id, str(log_file), err_holder)
            st.session_state.layer1_thread = t
            st.session_state["layer1_err_holder"] = err_holder
            time.sleep(0.3)
            st.rerun()

    # ── Approve Gate
    with bcol2:
        gate_disabled = not (stage == "gate_pending" and gate == "pending")
        g_label = "✅ Approve Gate"
        if gate == "approved": g_label = "✓ Approved"
        elif gate == "rejected": g_label = "✗ Rejected"
        if st.button(g_label, disabled=gate_disabled,
                     key=f"gate_{view_run_id}", use_container_width=True):
            m["approve_gate"](view_run_id)
            st.rerun()

    # ── Reject Gate
    with bcol3:
        if st.button("✗ Reject", disabled=gate_disabled,
                     key=f"reject_{view_run_id}", use_container_width=True):
            m["reject_gate"](view_run_id)
            st.rerun()

    # ── Run Layer 2
    with bcol4:
        l2_disabled = (
            layer2_running
            or gate != "approved"
            or status == "completed"
            or status == "cancelled"
        )
        l2_label = "▶ Run Layer 2"
        if layer2_running: l2_label = "⏳ Layer 2 running…"
        elif status == "completed": l2_label = "✓ Pipeline done"
        if st.button(l2_label, type="primary", disabled=l2_disabled,
                     key=f"l2_{view_run_id}", use_container_width=True):
            log_file = Path("runs") / view_run_id / "_live.log"
            err_holder = {"error": None}
            t = run_layer_in_background(m["run_layer2"], view_run_id, str(log_file), err_holder)
            st.session_state.layer2_thread = t
            st.session_state["layer2_err_holder"] = err_holder
            time.sleep(0.3)
            st.rerun()

    # Why-disabled hints
    if l1_disabled and not layer1_done and not layer1_running and status != "completed":
        st.caption(f"⚠️ Run Layer 1 disabled — status={status}, stage={stage}")

    # Show thread errors
    for layer_name in ("layer1", "layer2"):
        holder = st.session_state.get(f"{layer_name}_err_holder")
        if holder and holder.get("error"):
            st.error(f"❌ {layer_name} background thread crashed: {holder['error']}")
            if st.button(f"Clear {layer_name} error", key=f"clr_{layer_name}"):
                holder["error"] = None
                st.session_state[f"{layer_name}_thread"] = None
                st.rerun()

    st.markdown("---")

    # ── LIVE STATUS PANEL ──────────────────────────────────────────────────
    is_active = render_live_agent_panel(view_run_id)

    if is_active:
        render_live_log(view_run_id)
        time.sleep(2)
        st.rerun()
    else:
        log_file = Path("runs") / view_run_id / "_live.log"
        if log_file.exists() and log_file.stat().st_size > 0:
            with st.expander("🖥️ Last run console log", expanded=False):
                try:
                    st.code(log_file.read_text(encoding="utf-8", errors="replace")[-8000:],
                            language=None)
                except Exception:
                    pass

    st.markdown("---")

    # ── PER-AGENT TABS ─────────────────────────────────────────────────────
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
            c1.metric("Cost", f"${meta.get('cost_usd', 0) or 0:.4f}")
            c2.metric("LLM calls", meta.get("llm_calls", 0) or 0)
            c3.metric("LLM cache", meta.get("llm_cache_hits", 0) or 0)
            c4.metric("SERP calls", meta.get("serp_calls", 0) or 0)
            c5.metric("SERP cache", meta.get("serp_cache_hits", 0) or 0)

            if meta.get("validation_problems"):
                with st.expander("⚠️ Validation problems"):
                    for p in meta["validation_problems"]:
                        st.warning(p)

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
            st.warning("Layer 1 complete. Review the outputs in the other tabs, then approve.")
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
            c2.metric("Hubs", out.get("hub_count", 0) or 0)
            c3.metric("Spokes", out.get("spoke_count", 0) or 0)
            c4.metric("Sub-spokes", out.get("sub_spoke_count", 0) or 0)
            for art in articles:
                with st.expander(f"**[{art.get('type','?').upper()}]** {art.get('title','—')}"):
                    st.markdown(f"**Slug:** `/{art.get('slug','—')}`")
                    brief = art.get("writer_brief", {})
                    if brief:
                        st.markdown(f"**Angle:** {brief.get('angle','—')}")
                        for q in brief.get("must_answer", []):
                            st.markdown(f"- {q}")
        render_agent_section("content_architect")
    with agent_tabs[5]:
        out = m["load_agent_output"](view_run_id, "faq_architect")
        if out:
            c1, c2, c3 = st.columns(3)
            c1.metric("Input questions", out.get("total_input_questions", 0) or 0)
            c2.metric("Kept", out.get("kept_questions", 0) or 0)
            c3.metric("Dropped", out.get("dropped_count", 0) or 0)
        render_agent_section("faq_architect")
    with agent_tabs[6]:
        out = m["load_agent_output"](view_run_id, "research_prompt_generator")
        if out:
            prompt_text = out.get("master_research_prompt", "")
            st.metric("Prompt length", f"{len(prompt_text):,} chars")
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
        try:
            from dashboard_components.opportunity_view import render_opportunity_view
            render_opportunity_view(st.session_state.viewing_run_id, m)
        except Exception as e:
            st.error(f"Opportunity view failed: {e}")


# ─── Tab 3: Ingestion ─────────────────────────────────────────────────────
with main_tabs[2]:
    try:
        from dashboard_components.ingestion_view import render_ingestion_view
        render_ingestion_view(st.session_state.viewing_run_id)
    except Exception as e:
        st.error(f"Ingestion view failed: {e}")


# ─── Tab 4: Graph ─────────────────────────────────────────────────────────
with main_tabs[3]:
    try:
        from dashboard_components.graph_view import render_graph_view
        render_graph_view()
    except Exception as e:
        st.error(f"Graph view failed: {e}")


# ─── Tab 5: Artifacts Browser ─────────────────────────────────────────────
with main_tabs[4]:
    if not st.session_state.viewing_run_id:
        st.info("Open a run first.")
    else:
        view_run_id = st.session_state.viewing_run_id
        run = m["get_pipeline_run"](view_run_id)
        st.markdown(f"### Artifacts for `{view_run_id}`")
        st.caption(f"Disk path: `{run.get('artifact_path','')}`")

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

        artifacts = m["list_artifacts"](view_run_id)
        for agent_name, files in artifacts.items():
            with st.expander(f"📁 {agent_name} ({len(files)} files)"):
                for f in files:
                    st.markdown(
                        f"`{f['kind']}` · {f['byte_size']} bytes · "
                        f"`{f['file_path']}`"
                    )