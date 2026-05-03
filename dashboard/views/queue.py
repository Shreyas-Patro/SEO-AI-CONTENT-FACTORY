"""Topic queue: input multiple topics + run layers in parallel across topics."""
import threading
import time
from pathlib import Path
import streamlit as st

from dashboard.helpers import run_in_bg, is_alive


def render(m):
    st.markdown("### 🎯 Topic Queue — Parallel Pipeline")
    st.caption(
        "Add multiple topics. While Topic A's Layer 3 is running, Topic B's Layer 1 can start. "
        "This gives you ~2-3x throughput on a single machine."
    )

    if "queued_topics" not in st.session_state:
        st.session_state["queued_topics"] = []
    if "queue_threads" not in st.session_state:
        st.session_state["queue_threads"] = {}

    # ── Input new topics ──
    with st.form("queue_form"):
        topics_input = st.text_area(
            "Enter topics (one per line):",
            placeholder="HSR Layout\nWhitefield\nElectronic City\n",
            height=150,
        )
        submit = st.form_submit_button("➕ Queue topics", type="primary")
        if submit:
            new = [t.strip() for t in topics_input.split("\n") if t.strip()]
            for topic in new:
                run_id = m["start_pipeline_run"](topic)
                st.session_state["queued_topics"].append({
                    "run_id": run_id,
                    "topic": topic,
                    "status": "queued",
                })
            if new:
                st.success(f"Queued {len(new)} topic(s)")
                st.rerun()

    if not st.session_state["queued_topics"]:
        st.info("Queue is empty. Add topics above.")
        return

    # ── Show queue ──
    st.markdown("### 📋 Queue")
    for i, item in enumerate(st.session_state["queued_topics"]):
        rid = item["run_id"]
        run = m["get_pipeline_run"](rid)
        if not run:
            continue
        stage = run.get("current_stage", "init")
        gate = run.get("gate_status", "pending")

        # Determine state
        l1_thread = st.session_state["queue_threads"].get(f"{rid}_l1")
        l2_thread = st.session_state["queue_threads"].get(f"{rid}_l2")
        l3_thread = st.session_state["queue_threads"].get(f"{rid}_l3")

        any_alive = is_alive(l1_thread) or is_alive(l2_thread) or is_alive(l3_thread)

        cols = st.columns([3, 2, 1, 1, 1, 1])
        cols[0].markdown(f"**{item['topic']}** `{rid[-8:]}`")
        cols[1].caption(f"Stage: `{stage}` · Gate: `{gate}`")

        # Auto-fire Layer 1 if not started
        with cols[2]:
            if stage == "init" and not is_alive(l1_thread):
                if st.button("▶ L1", key=f"qbtn_l1_{rid}", use_container_width=True):
                    log = Path("runs") / rid / "_live.log"
                    err = {"error": None}
                    t = run_in_bg(m["run_layer1"], rid, str(log), err)
                    st.session_state["queue_threads"][f"{rid}_l1"] = t
                    st.session_state["queue_threads"][f"{rid}_l1_err"] = err
                    time.sleep(.3)
                    st.rerun()
            elif is_alive(l1_thread):
                st.markdown("⏳ L1")
            elif stage in ("gate_pending", "gate_approved"):
                st.markdown("✓ L1")

        with cols[3]:
            if gate == "approved" and not is_alive(l2_thread) and stage in ("gate_approved",):
                if st.button("▶ L2", key=f"qbtn_l2_{rid}", use_container_width=True):
                    log = Path("runs") / rid / "_live.log"
                    err = {"error": None}
                    t = run_in_bg(m["run_layer2"], rid, str(log), err)
                    st.session_state["queue_threads"][f"{rid}_l2"] = t
                    time.sleep(.3)
                    st.rerun()
            elif is_alive(l2_thread):
                st.markdown("⏳ L2")
            elif stage in ("layer2_done", "layer3_writing", "done"):
                st.markdown("✓ L2")

        with cols[4]:
            if stage == "layer2_done" and not is_alive(l3_thread):
                if st.button("▶ L3", key=f"qbtn_l3_{rid}", use_container_width=True):
                    log = Path("runs") / rid / "_live.log"
                    err = {"error": None}
                    t = run_in_bg(m["run_layer3"], rid, str(log), err)
                    st.session_state["queue_threads"][f"{rid}_l3"] = t
                    time.sleep(.3)
                    st.rerun()
            elif is_alive(l3_thread):
                st.markdown("⏳ L3")
            elif stage == "done":
                st.markdown("✓ L3")

        with cols[5]:
            if st.button("Open", key=f"qopen_{rid}", use_container_width=True):
                st.session_state["viewing_run_id"] = rid
                st.success("Opened. Switch to Pipeline tab.")

    # Smart refresh — only if any topic has an alive thread
    any_running = any(
        is_alive(st.session_state["queue_threads"].get(k))
        for k in st.session_state["queue_threads"]
    )
    if any_running:
        time.sleep(3)
        st.rerun()