"""Pipeline tab: run controls + agent status panel + live log."""
import time
import json
from pathlib import Path
import streamlit as st

AGENT_ORDER_L1 = [
    ("trend_scout", "📡 Trend Scout"),
    ("competitor_spy", "🕵️ Comp Spy"),
    ("keyword_mapper", "🗺️ KW Mapper"),
]
AGENT_ORDER_L2 = [
    ("content_architect", "🏗️ Content Architect"),
    ("faq_architect", "❓ FAQs"),
    ("research_prompt_generator", "🔬 Research Prompt"),
]


def _agent_status(run_id, agent_name):
    base = Path("runs") / run_id / agent_name
    if not base.exists(): return "pending"
    if (base / "output.json").exists():
        meta_f = base / "metadata.json"
        if meta_f.exists():
            try:
                d = json.loads(meta_f.read_text())
                if d.get("status") == "failed": return "failed"
            except Exception: pass
        return "done"
    if (base / "input.json").exists(): return "active"
    return "pending"


def _render_agent_card(run_id, key, label, m):
    status = _agent_status(run_id, key)
    if status == "active":
        st.markdown(
            f'<div class="agent-card agent-active">'
            f'<div class="agent-name">{label}</div>'
            f'<div class="agent-detail"><span class="pulse"></span>running</div></div>',
            unsafe_allow_html=True)
    elif status == "done":
        meta = m["load_agent_metadata"](run_id, key) or {}
        cost = meta.get("cost_usd", 0) or 0
        dur = meta.get("duration_seconds", 0) or 0
        st.markdown(
            f'<div class="agent-card agent-done">'
            f'<div class="agent-name">{label} ✓</div>'
            f'<div class="agent-detail">${cost:.4f} · {dur:.1f}s</div></div>',
            unsafe_allow_html=True)
    elif status == "failed":
        st.markdown(
            f'<div class="agent-card agent-failed">'
            f'<div class="agent-name">{label} ✗</div>'
            f'<div class="agent-detail">failed</div></div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div class="agent-card agent-pending">'
            f'<div class="agent-name">{label}</div>'
            f'<div class="agent-detail">waiting</div></div>',
            unsafe_allow_html=True)


def _render_live_log(run_id):
    log_file = Path("runs") / run_id / "_live.log"
    if not log_file.exists():
        st.caption("(waiting for log...)"); return
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception: return
    st.code(text[-7000:] if len(text) > 7000 else text or "(no output yet)", language=None)


def _render_agent_detail(run_id, agent_key, m):
    inp = m["load_agent_input"](run_id, agent_key)
    out = m["load_agent_output"](run_id, agent_key)
    meta = m["load_agent_metadata"](run_id, agent_key)
    console = m["load_agent_console"](run_id, agent_key)

    if not (inp or out or console):
        st.info(f"{agent_key} has not run yet."); return

    if meta and isinstance(meta, dict):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Cost", f"${meta.get('cost_usd', 0) or 0:.4f}")
        c2.metric("LLM", meta.get('llm_calls', 0) or 0)
        c3.metric("SERP", meta.get('serp_calls', 0) or 0)
        c4.metric("Duration", f"{meta.get('duration_seconds', 0) or 0:.1f}s")
        c5.metric("Attempts", meta.get('attempts', 1) or 1)
        for p in (meta.get("validation_problems") or []):
            st.warning(p)

    if agent_key != "trend_scout":
        if st.button(f"🔄 Rerun {agent_key}", key=f"rerun_{agent_key}_{run_id}"):
            try:
                with st.spinner(f"Rerunning {agent_key}..."):
                    m["rerun_agent"](run_id, agent_key)
                st.success("Done"); st.rerun()
            except Exception as e:
                st.error(f"Rerun failed: {e}")

    with st.expander("📥 Input"):
        if inp:
            try: st.json(inp)
            except Exception: st.code(str(inp)[:3000])
        else:
            st.caption("No input")

    with st.expander("📤 Output", expanded=True):
        edit_key = f"edit_{agent_key}_{run_id}"
        if st.button("✏️ Edit", key=f"btn_{edit_key}"):
            st.session_state[edit_key] = True
        if st.session_state.get(edit_key):
            default = json.dumps(out, indent=2, default=str) if out else "{}"
            edited = st.text_area("JSON:", default, height=400, key=f"ta_{edit_key}")
            sc, cc = st.columns(2)
            with sc:
                if st.button("💾 Save", key=f"save_{edit_key}"):
                    try:
                        m["edit_agent_output"](run_id, agent_key, json.loads(edited))
                        st.session_state[edit_key] = False
                        st.success("Saved"); st.rerun()
                    except Exception as e:
                        st.error(f"Invalid JSON: {e}")
            with cc:
                if st.button("Cancel", key=f"cancel_{edit_key}"):
                    st.session_state[edit_key] = False; st.rerun()
        else:
            if out:
                try: st.json(out)
                except Exception: st.code(str(out)[:5000])
            else:
                st.caption("No output")

    if console:
        with st.expander("🖥️ Console"):
            st.code(console[-5000:], language=None)


def render_pipeline_view(m, run_in_bg, is_alive):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Start a new run or open one from the sidebar."); return

    run = m["get_pipeline_run"](rid)
    if not run:
        st.error("Run not found"); return

    stage = run.get("current_stage") or "init"
    gate = run.get("gate_status") or "pending"
    status = run.get("status") or "running"

    st.markdown(f"**Topic:** `{run['topic']}` · **Stage:** `{stage}` · **Gate:** `{gate}`")

    l1_alive = is_alive(st.session_state.layer1_thread)
    l2_alive = is_alive(st.session_state.layer2_thread)
    l3_alive = is_alive(st.session_state.layer3_thread)

    bcol = st.columns(5)

    # Layer 1
    with bcol[0]:
        l1_done = stage in ("gate_pending","gate_approved","layer2_content_architect",
                            "layer2_faq_architect","layer2_research_prompt",
                            "layer2_done","layer3_writing","done")
        l1_off = l1_alive or l1_done or status in ("completed","cancelled")
        l1_lbl = "⏳ Running…" if l1_alive else ("✓ L1 Done" if l1_done else "▶ Layer 1")
        if st.button(l1_lbl, type="primary", disabled=l1_off, key=f"l1_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer1"], rid, str(log), err)
            st.session_state.layer1_thread = t
            st.session_state["l1_err"] = err
            time.sleep(.3); st.rerun()

    # Approve / Reject
    with bcol[1]:
        g_off = not (stage == "gate_pending" and gate == "pending")
        g_lbl = "✓ Approved" if gate == "approved" else ("✗ Rejected" if gate == "rejected" else "✅ Approve")
        if st.button(g_lbl, disabled=g_off, key=f"g_{rid}", use_container_width=True):
            m["approve_gate"](rid); st.rerun()

    with bcol[2]:
        if st.button("✗ Reject", disabled=g_off, key=f"rj_{rid}", use_container_width=True):
            m["reject_gate"](rid); st.rerun()

    # Layer 2
    with bcol[3]:
        l2_done = stage in ("layer2_done","layer3_writing","done")
        l2_off = l2_alive or gate != "approved" or status in ("completed","cancelled") or l2_done
        l2_lbl = "⏳ Running…" if l2_alive else ("✓ L2 Done" if l2_done else "▶ Layer 2")
        if st.button(l2_lbl, type="primary", disabled=l2_off, key=f"l2_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer2"], rid, str(log), err)
            st.session_state.layer2_thread = t
            st.session_state["l2_err"] = err
            time.sleep(.3); st.rerun()

    # Layer 3
    with bcol[4]:
        l3_off = l3_alive or not run.get("cluster_id") or status in ("completed","cancelled")
        l3_lbl = "⏳ Writing…" if l3_alive else "▶ Layer 3"
        if status == "completed": l3_lbl = "✓ Done"
        if st.button(l3_lbl, type="primary", disabled=l3_off, key=f"l3_{rid}", use_container_width=True):
            log = Path("runs") / rid / "_live.log"
            err = {"error": None}
            t = run_in_bg(m["run_layer3"], rid, str(log), err)
            st.session_state.layer3_thread = t
            st.session_state["l3_err"] = err
            time.sleep(.3); st.rerun()

    # Errors
    for ln in ("l1","l2","l3"):
        h = st.session_state.get(f"{ln}_err")
        if h and h.get("error"):
            st.error(f"❌ {ln} crashed: {h['error']}")
            if st.button("Clear", key=f"clr_{ln}"):
                h["error"] = None; st.rerun()

    st.markdown("---")

    # ── Agent status grid ──
    st.markdown("### 🤖 Agent Activity")
    st.caption("Layer 1 — Discovery")
    l1_cols = st.columns(len(AGENT_ORDER_L1))
    for i, (k, lbl) in enumerate(AGENT_ORDER_L1):
        with l1_cols[i]:
            _render_agent_card(rid, k, lbl, m)

    st.caption("Layer 2 — Architecture")
    l2_cols = st.columns(len(AGENT_ORDER_L2))
    for i, (k, lbl) in enumerate(AGENT_ORDER_L2):
        with l2_cols[i]:
            _render_agent_card(rid, k, lbl, m)

    st.markdown("---")

    # Live log while running
    if any([l1_alive, l2_alive, l3_alive]):
        st.markdown("### 📡 Live Console")
        _render_live_log(rid)
    else:
        log_file = Path("runs") / rid / "_live.log"
        if log_file.exists() and log_file.stat().st_size > 0:
            with st.expander("🖥️ Last console log"):
                try: st.code(log_file.read_text(encoding="utf-8", errors="replace")[-8000:])
                except Exception: pass

    st.markdown("---")

    # Per-agent detail tabs
    atabs = st.tabs(["📡 Trend", "🕵️ Comp", "🗺️ Kw", "🚪 Gate",
                     "🏗️ Arch", "❓ FAQ", "🔬 Research"])
    with atabs[0]: _render_agent_detail(rid, "trend_scout", m)
    with atabs[1]: _render_agent_detail(rid, "competitor_spy", m)
    with atabs[2]: _render_agent_detail(rid, "keyword_mapper", m)
    with atabs[3]:
        st.markdown("### Human Approval Gate")
        if gate == "pending" and stage == "gate_pending":
            st.warning("Layer 1 complete. Review outputs then approve.")
        elif gate == "approved": st.success("Gate approved — Layer 2 ready.")
        elif gate == "rejected": st.error("Gate rejected.")
        else: st.info("Run Layer 1 first.")
    with atabs[4]: _render_agent_detail(rid, "content_architect", m)
    with atabs[5]: _render_agent_detail(rid, "faq_architect", m)
    with atabs[6]: _render_agent_detail(rid, "research_prompt_generator", m)