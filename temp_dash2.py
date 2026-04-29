"""
Canvas Homes — AI Pipeline Dashboard v5

FEATURES:
- Layer 1/2/3 controls with live progress
- Per-agent rerun buttons
- Real-time activity log
- Article writing pipeline view
- Export controls
- Persistent runs (not session-based)
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
    page_title="Canvas Homes · AI Pipeline v5",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── STYLES ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
.block-container { padding: 1rem 2rem 2rem; }
section[data-testid="stSidebar"] { background: #0f0f1a; border-right: 1px solid #1f1f35; }
section[data-testid="stSidebar"] .stButton button {
    background: #c8ff00; color: #000 !important; font-weight: 700;
    border: none; border-radius: 8px; width: 100%; font-size: 13px;
}
[data-testid="metric-container"] {
    background: #161624; border: 1px solid #1f1f35;
    border-radius: 10px; padding: 12px 14px;
}
.status-box {
    background: #0a1f0a; border: 1px solid #1a4a1a; border-radius: 10px;
    padding: 10px 12px; margin: 3px 0; min-height: 60px;
}
.status-active {
    background: linear-gradient(90deg, #1a3a1a 0%, #0a1f0a 100%);
    border: 2px solid #00ff88; box-shadow: 0 0 12px rgba(0,255,136,0.3);
}
.status-pending { background: #161624; border: 1px solid #1f1f35; opacity: 0.5; }
.status-done { background: #0a1a2a; border: 1px solid #1f3a5a; }
.status-failed { background: #2a0a0a; border: 1px solid #5a1f1f; }
.agent-name { font-weight: 700; font-size: 11px; color: #fff; }
.agent-detail { font-family: monospace; font-size: 9px; color: #aaa; margin-top: 3px; }
.pulse {
    display: inline-block; width: 7px; height: 7px; border-radius: 50%;
    background: #00ff88; box-shadow: 0 0 6px #00ff88;
    animation: pulse 1.4s ease-in-out infinite; margin-right: 5px;
}
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.5;transform:scale(1.3)} }
</style>
""", unsafe_allow_html=True)


# ── SESSION STATE ──────────────────────────────────────────────────────────
def _init():
    for k, v in {"topic": "Hosa Road", "current_run_id": None, "viewing_run_id": None,
                  "layer1_thread": None, "layer2_thread": None, "layer3_thread": None,
                  "thread_error": None}.items():
        if k not in st.session_state:
            st.session_state[k] = v
_init()


# ── TEE LOGGING ────────────────────────────────────────────────────────────
class _Tee:
    def __init__(self, log_file, original):
        self.log_file = log_file
        self.original = original
    def write(self, s):
        try: self.original.write(s)
        except: pass
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(s); f.flush()
        except: pass
        return len(s) if s else 0
    def flush(self):
        try: self.original.flush()
        except: pass

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
            except: pass
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


# ── LOAD MODULES ───────────────────────────────────────────────────────────
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
            get_artifact_path, load_state,
        )
        from db.pipeline_state import StateKeys
        return {
            "start_pipeline_run": start_pipeline_run,
            "run_layer1": run_layer1, "run_layer2": run_layer2,
            "run_layer3": run_layer3,
            "approve_gate": approve_gate, "reject_gate": reject_gate,
            "rerun_agent": rerun_agent,
            "load_agent_output": load_agent_output,
            "load_agent_input": load_agent_input,
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


# ── HELPERS ────────────────────────────────────────────────────────────────
AGENT_ORDER = [
    ("trend_scout", "📡 Trend Scout"),
    ("competitor_spy", "🕵️ Comp Spy"),
    ("keyword_mapper", "🗺️ KW Mapper"),
    ("content_architect", "🏗️ Content Architect"),
    ("faq_architect", "❓ FAQs"),
    ("research_prompt_generator", "🔬 Research"),
]

def is_alive(t):
    return t is not None and t.is_alive()

def get_agent_status(run_id, agent_name):
    base = Path("runs") / run_id / agent_name
    if not base.exists(): return "pending"
    meta = base / "metadata.json"
    out = base / "output.json"
    if out.exists():
        if meta.exists():
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                if d.get("status") == "failed": return "failed"
            except: pass
        return "done"
    if (base / "input.json").exists(): return "active"
    return "pending"

def render_agent_panel(run_id):
    is_running = any(is_alive(st.session_state.get(f"layer{i}_thread")) for i in (1,2,3))
    cols = st.columns(len(AGENT_ORDER))
    for i, (key, label) in enumerate(AGENT_ORDER):
        status = get_agent_status(run_id, key)
        with cols[i]:
            if status == "active":
                st.markdown(f'<div class="status-box status-active"><div class="agent-name">{label}</div>'
                           f'<div class="agent-detail"><span class="pulse"></span>running</div></div>',
                           unsafe_allow_html=True)
            elif status == "done":
                meta = m["load_agent_metadata"](run_id, key) or {}
                cost = meta.get("cost_usd", 0) or 0
                st.markdown(f'<div class="status-box status-done"><div class="agent-name">{label} ✓</div>'
                           f'<div class="agent-detail">${cost:.4f}</div></div>',
                           unsafe_allow_html=True)
            elif status == "failed":
                st.markdown(f'<div class="status-box status-failed"><div class="agent-name">{label} ✗</div>'
                           f'<div class="agent-detail">failed</div></div>',
                           unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="status-box status-pending"><div class="agent-name">{label}</div>'
                           f'<div class="agent-detail">waiting</div></div>',
                           unsafe_allow_html=True)
    return is_running


def render_live_log(run_id):
    log_file = Path("runs") / run_id / "_live.log"
    if not log_file.exists():
        st.caption("(waiting for log…)")
        return
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except: return
    tail = text[-6000:] if len(text) > 6000 else text
    st.code(tail or "(no output yet)", language=None)


def render_agent_detail(run_id, agent_key, allow_rerun=True):
    """Render input/output/console for one agent."""
    inp = m["load_agent_input"](run_id, agent_key)
    out = m["load_agent_output"](run_id, agent_key)
    meta = m["load_agent_metadata"](run_id, agent_key)
    console = m["load_agent_console"](run_id, agent_key)

    if not out and not inp and not console:
        st.info(f"{agent_key} has not run yet.")
        return

    # Metrics
    if meta and isinstance(meta, dict):
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("Cost", f"${meta.get('cost_usd', 0) or 0:.4f}")
        mc2.metric("LLM", meta.get("llm_calls", 0) or 0)
        mc3.metric("SERP", meta.get("serp_calls", 0) or 0)
        mc4.metric("Duration", f"{meta.get('duration_seconds', 0) or 0:.1f}s")
        mc5.metric("Attempts", meta.get("attempts", 1) or 1)
        vp = meta.get("validation_problems")
        if vp:
            for p in vp:
                st.warning(p)

    # Rerun
    if allow_rerun and agent_key != "trend_scout":
        rerun_key = f"rerun_{agent_key}_{run_id}"
        if st.button(f"🔄 Rerun {agent_key}", key=rerun_key):
            try:
                with st.spinner(f"Rerunning {agent_key}..."):
                    m["rerun_agent"](run_id, agent_key)
                st.success(f"✅ {agent_key} rerun complete")
                st.rerun()
            except Exception as e:
                st.error(f"Rerun failed: {e}")

    # Input expander
    inp_expander = st.expander("📥 Input", expanded=False)
    with inp_expander:
        if inp is not None and inp:
            try:
                st.json(inp)
            except Exception:
                st.code(str(inp)[:3000])
        else:
            st.caption("No input")

    # Output expander
    out_expander = st.expander("📤 Output", expanded=True)
    with out_expander:
        edit_key = f"edit_{agent_key}_{run_id}"
        if st.button("✏️ Edit", key=f"btn_{edit_key}"):
            st.session_state[edit_key] = True

        if st.session_state.get(edit_key):
            try:
                default_val = json.dumps(out, indent=2, default=str) if out else "{}"
            except Exception:
                default_val = "{}"
            edited = st.text_area("JSON:", value=default_val, height=400, key=f"ta_{edit_key}")
            save_col, cancel_col = st.columns(2)
            with save_col:
                if st.button("💾 Save", key=f"save_{edit_key}"):
                    try:
                        m["edit_agent_output"](run_id, agent_key, json.loads(edited))
                        st.session_state[edit_key] = False
                        st.success("Saved")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
            with cancel_col:
                if st.button("Cancel", key=f"cancel_{edit_key}"):
                    st.session_state[edit_key] = False
                    st.rerun()
        else:
            if out is not None and out:
                try:
                    st.json(out)
                except Exception:
                    st.code(str(out)[:5000])
            else:
                st.caption("No output")

    # Console expander
    if console:
        con_expander = st.expander("🖥️ Console")
        with con_expander:
            st.code(console[-5000:], language=None)

# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏠 Canvas Homes")
    st.caption("AI PIPELINE v5")

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
        labels = [f"{r['id'][-8:]} · {r['topic'][:16]} · {r['status']}" for r in runs]
        idx = st.selectbox("Past runs", range(len(runs)), format_func=lambda i: labels[i],
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
            st.markdown(f"**{run['status']}** · {run.get('current_stage') or 'init'}")
            st.markdown(f"💰 ${run.get('total_cost_usd',0) or 0:.4f}")
            st.markdown(f"🔍 {run.get('total_serp_calls',0) or 0} SERP · 🤖 {run.get('total_llm_calls',0) or 0} LLM")


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════
st.markdown("# 🏠 Canvas Homes AI Pipeline")

tabs = st.tabs(["🚀 Pipeline", "📝 Articles", "📊 Opportunity", "📥 Ingestion", "🕸️ Graph", "🗂️ Artifacts"])

# ─── TAB 1: Pipeline ──────────────────────────────────────────────────────
with tabs[0]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Start a new run or open a past run from the sidebar.")
        st.stop()

    run = m["get_pipeline_run"](rid)
    if not run:
        st.error("Run not found")
        st.stop()

    stage = run.get("current_stage") or "init"
    gate = run.get("gate_status") or "pending"
    status = run.get("status") or "running"

    st.markdown(f"**Topic:** `{run['topic']}`  ·  **Stage:** `{stage}`  ·  **Gate:** `{gate}`")

    # ── ACTION BAR ─────────────────────────────────────────────────────
    l1_alive = is_alive(st.session_state.layer1_thread)
    l2_alive = is_alive(st.session_state.layer2_thread)
    l3_alive = is_alive(st.session_state.layer3_thread)

    bcol1, bcol2, bcol3, bcol4, bcol5 = st.columns(5)

    # Layer 1
    with bcol1:
        l1_done = stage in ("gate_pending","gate_approved","layer2_content_architect",
                            "layer2_faq_architect","layer2_research_prompt_gen",
                            "layer2_done","layer3_writing","done")
        l1_off = l1_alive or l1_done or status in ("completed","cancelled")
        l1_lbl = "⏳ Running…" if l1_alive else ("✓ L1 Done" if l1_done else "▶ Run Layer 1")
        if st.button(l1_lbl, type="primary", disabled=l1_off, key=f"l1_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer1"], rid, str(log), err)
            st.session_state.layer1_thread = t
            st.session_state["l1_err"] = err
            time.sleep(0.3); st.rerun()

    # Approve
    with bcol2:
        g_off = not (stage == "gate_pending" and gate == "pending")
        g_lbl = "✓ Approved" if gate == "approved" else ("✗ Rejected" if gate == "rejected" else "✅ Approve")
        if st.button(g_lbl, disabled=g_off, key=f"g_{rid}", use_container_width=True):
            m["approve_gate"](rid); st.rerun()

    # Reject
    with bcol3:
        if st.button("✗ Reject", disabled=g_off, key=f"rj_{rid}", use_container_width=True):
            m["reject_gate"](rid); st.rerun()

    # Layer 2
    with bcol4:
        l2_done = stage in ("layer2_done","layer3_writing","done")
        l2_off = l2_alive or gate != "approved" or status in ("completed","cancelled") or l2_done
        l2_lbl = "⏳ Running…" if l2_alive else ("✓ L2 Done" if l2_done else "▶ Run Layer 2")
        if st.button(l2_lbl, type="primary", disabled=l2_off, key=f"l2_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer2"], rid, str(log), err)
            st.session_state.layer2_thread = t
            st.session_state["l2_err"] = err
            time.sleep(0.3); st.rerun()

    # Layer 3
    with bcol5:
        l3_off = l3_alive or not run.get("cluster_id") or status in ("completed","cancelled")
        l3_lbl = "⏳ Writing…" if l3_alive else "▶ Run Layer 3"
        if status == "completed": l3_lbl = "✓ Done"
        if st.button(l3_lbl, type="primary", disabled=l3_off, key=f"l3_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer3"], rid, str(log), err)
            st.session_state.layer3_thread = t
            st.session_state["l3_err"] = err
            time.sleep(0.3); st.rerun()

    # Errors
    for ln in ("l1","l2","l3"):
        holder = st.session_state.get(f"{ln}_err")
        if holder and holder.get("error"):
            st.error(f"❌ {ln} crashed: {holder['error']}")
            if st.button(f"Clear", key=f"clr_{ln}"):
                holder["error"] = None; st.rerun()

    st.markdown("---")

    # ── AGENT STATUS PANEL ─────────────────────────────────────────────
    is_running = render_agent_panel(rid)

    if is_running:
        render_live_log(rid)
        time.sleep(2)
        st.rerun()
    else:
        log_file = Path("runs") / rid / "_live.log"
        if log_file.exists() and log_file.stat().st_size > 0:
            with st.expander("🖥️ Last console log"):
                try: st.code(log_file.read_text(encoding="utf-8", errors="replace")[-8000:])
                except: pass

    st.markdown("---")

    # ── PER-AGENT TABS ─────────────────────────────────────────────────
    atabs = st.tabs(["📡 Trend", "🕵️ Comp", "🗺️ Kw", "🚪 Gate",
                     "🏗️ Arch", "❓ FAQ", "🔬 Research"])

    with atabs[0]: render_agent_detail(rid, "trend_scout")
    with atabs[1]: render_agent_detail(rid, "competitor_spy")
    with atabs[2]: render_agent_detail(rid, "keyword_mapper")
    with atabs[3]:
        st.markdown("### Human Approval Gate")
        if gate == "pending" and stage == "gate_pending":
            st.warning("Layer 1 complete. Review outputs then approve.")
        elif gate == "approved": st.success("Gate approved — Layer 2 ready.")
        elif gate == "rejected": st.error("Gate rejected.")
        else: st.info("Run Layer 1 first.")
    with atabs[4]:
        out = m["load_agent_output"](rid, "content_architect")
        if out:
            plan = out.get("cluster_plan", {})
            articles = plan.get("articles", [])
            c1, c2 = st.columns(2)
            c1.metric("Articles planned", len(articles))
            c2.metric("Created in DB", out.get("articles_created", 0))
            for art in articles:
                with st.expander(f"[{art.get('type','?').upper()}] {art.get('title','—')}"):
                    st.markdown(f"**Slug:** `/{art.get('slug','—')}`")
                    st.markdown(f"**Words:** {art.get('word_count_target', '—')}")
                    if art.get("outline"):
                        st.markdown("**Outline:**")
                        for h in art["outline"][:10]:
                            st.markdown(f"  {h}")
        render_agent_detail(rid, "content_architect")
    with atabs[5]: render_agent_detail(rid, "faq_architect")
    with atabs[6]:
        out = m["load_agent_output"](rid, "research_prompt_generator")
        if out:
            prompt_text = out.get("master_research_prompt", "")
            st.metric("Prompt length", f"{len(prompt_text):,} chars")
            st.code(prompt_text[:3000], language="markdown")
            if prompt_text:
                st.download_button("💾 Download prompt", prompt_text,
                                   file_name=f"research_{run['topic'].replace(' ','_')}.txt")
        render_agent_detail(rid, "research_prompt_generator")


# ─── TAB 2: Articles ──────────────────────────────────────────────────────
with tabs[1]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
    else:
        run = m["get_pipeline_run"](rid)
        cluster_id = run.get("cluster_id") if run else None

        if not cluster_id:
            st.info("Run Layer 2 first to create articles.")
        else:
            try:
                from db.sqlite_ops import get_articles_by_cluster
                articles = get_articles_by_cluster(cluster_id)
                st.markdown(f"### 📝 Articles ({len(articles)} in cluster)")

                # Summary metrics
                written = sum(1 for a in articles if a.get("status") == "written")
                planned = sum(1 for a in articles if a.get("status") == "planned")
                c1, c2, c3 = st.columns(3)
                c1.metric("Total", len(articles))
                c2.metric("Written", written)
                c3.metric("Planned", planned)

                for art in articles:
                    status_icon = {"planned": "📋", "written": "✅", "published": "🌐"}.get(art["status"], "❔")
                    with st.expander(
                        f"{status_icon} [{art['article_type'].upper()}] {art['title']}"
                        f" — {art.get('word_count', 0)} words"
                    ):
                        st.markdown(f"**ID:** `{art['id']}`  ·  **Slug:** `/{art['slug']}`  ·  **Status:** {art['status']}")

                        if art.get("content_md"):
                            st.markdown("---")
                            st.markdown(art["content_md"][:2000])
                            if len(art["content_md"]) > 2000:
                                st.caption(f"... ({len(art['content_md'])} chars total)")

                        if art.get("fact_check_score"):
                            st.markdown(f"**Fact score:** {art['fact_check_score']}")
                        if art.get("brand_tone_score"):
                            st.markdown(f"**Brand score:** {art['brand_tone_score']}")
                        if art.get("meta_title"):
                            st.markdown(f"**Meta title:** {art['meta_title']}")

                        # Article history
                        history = json.loads(art.get("history", "[]") or "[]")
                        if history:
                            st.markdown("**History:**")
                            for h in history:
                                st.caption(f"`{h['stage']}` @ {h['timestamp'][:19]} — {h['changes_summary']}")

            except Exception as e:
                st.error(f"Failed to load articles: {e}")


# ─── TAB 3: Opportunity ───────────────────────────────────────────────────
with tabs[2]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
    else:
        try:
            from dashboard_components.opportunity_view import render_opportunity_view
            render_opportunity_view(rid, m)
        except Exception as e:
            st.error(f"Opportunity view error: {e}")


# ─── TAB 4: Ingestion ─────────────────────────────────────────────────────
with tabs[3]:
    try:
        from dashboard_components.ingestion_view import render_ingestion_view
        render_ingestion_view(st.session_state.viewing_run_id)
    except Exception as e:
        st.error(f"Ingestion error: {e}")


# ─── TAB 5: Graph ─────────────────────────────────────────────────────────
with tabs[4]:
    try:
        from dashboard_components.graph_view import render_graph_view
        render_graph_view()
    except Exception as e:
        st.error(f"Graph error: {e}")


# ─── TAB 6: Artifacts ─────────────────────────────────────────────────────
with tabs[5]:
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
    else:
        run = m["get_pipeline_run"](rid)
        st.markdown(f"### Artifacts for `{rid}`")

        with st.expander("🧠 PipelineState"):
            state = m["load_state"](rid)
            st.markdown(f"**Stage:** `{state.get('stage','?')}`")
            st.markdown(f"**Completed:** {state.get('agents_completed', [])}")
            shared_keys = list(state.get("shared", {}).keys())
            if shared_keys:
                key_to_edit = st.selectbox("Edit key", shared_keys, key="state_edit_sel")
                edited = st.text_area("Value:", json.dumps(state["shared"][key_to_edit], indent=2, default=str),
                                      height=250, key=f"state_ta_{key_to_edit}")
                if st.button("💾 Save", key="state_save"):
                    try:
                        m["edit_state_key"](rid, key_to_edit, json.loads(edited))
                        st.success("Saved"); st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")

        artifacts = m["list_artifacts"](rid)
        for agent_name, files in artifacts.items():
            with st.expander(f"📁 {agent_name} ({len(files)} files)"):
                for f in files:
                    st.caption(f"`{f['kind']}` · {f['byte_size']} bytes · `{f['file_path']}`")