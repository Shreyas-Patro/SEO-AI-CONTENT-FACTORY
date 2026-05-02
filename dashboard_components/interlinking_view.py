"""Interlinking tab: 2-stage flow (cluster → global) with human approval between each."""
import json
import subprocess
import sys
from pathlib import Path
import streamlit as st

from db.sqlite_ops import get_articles_by_cluster


def render_interlinking_view(m):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first."); return

    run = m["get_pipeline_run"](rid)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        st.info("Run Layer 2 first."); return

    st.markdown("### 🔗 Interlinking")
    st.caption("Two-stage flow: link articles inside this cluster, then link the cluster against the global corpus.")

    articles = get_articles_by_cluster(cluster_id)
    written = [a for a in articles if (a.get("content_md") or "").strip()]
    if not written:
        st.warning("No written articles yet."); return
    st.metric("Articles ready for interlinking", len(written))

    st.markdown("---")
    st.markdown("#### Stage 1 — Cluster-level pass")
    st.caption("Find and approve internal links between articles in this cluster.")

    cs1, cs2 = st.columns([2, 1])
    with cs1:
        if st.button("🔗 Run cluster interlinking", type="primary", key="run_cluster_link"):
            try:
                from link_engine_bridge import cluster_pass
                with st.spinner("Running link engine on cluster..."):
                    result = cluster_pass(cluster_id, rid)
                st.session_state[f"cluster_link_result_{rid}"] = result
                st.success(f"✓ Found {len(result.get('report', []))} candidate links")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")
    with cs2:
        if st.button("🚀 Open Link Engine UI"):
            st.code("python -m link_engine.cli dashboard", language="bash")
            st.caption("Run that to open the link engine's review dashboard.")

    cluster_result = st.session_state.get(f"cluster_link_result_{rid}")
    if cluster_result:
        report = cluster_result.get("report", [])
        if not report:
            st.info("No links found in cluster pass.")
        else:
            st.markdown(f"**{len(report)} candidate cluster links found.**")
            with st.expander("📋 Review candidates"):
                for link in report[:50]:
                    st.markdown(
                        f"- **{link.get('source_article_title','?')}** → "
                        f"**{link.get('target_article_title','?')}**  \n"
                        f"  anchor: `{link.get('anchor_text','?')}` · "
                        f"sim: `{link.get('similarity_score',0):.2f}` · "
                        f"confidence: `{link.get('llm_confidence','?')}`"
                    )
            st.success("Use the Link Engine UI to approve/reject each link, then inject.")

    st.markdown("---")
    st.markdown("#### Stage 2 — Global pass")
    st.caption("Once cluster links are injected, find connections to all previously published articles.")

    gs1, gs2 = st.columns([2, 1])
    with gs1:
        if st.button("🌍 Run global interlinking", type="primary", key="run_global_link"):
            try:
                from link_engine_bridge import global_pass
                with st.spinner("Running link engine against global corpus..."):
                    result = global_pass(cluster_id, rid)
                st.session_state[f"global_link_result_{rid}"] = result
                st.success(f"✓ Global pass complete — {len(result.get('report', []))} candidate links")
                st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")
    with gs2:
        if st.button("📤 Publish cluster"):
            try:
                from link_engine_bridge import publish_cluster
                count = publish_cluster(cluster_id)
                st.success(f"Published {count} articles to global corpus")
            except Exception as e:
                st.error(f"Failed: {e}")

    global_result = st.session_state.get(f"global_link_result_{rid}")
    if global_result:
        report = global_result.get("report", [])
        st.markdown(f"**{len(report)} global candidate links.**")
        with st.expander("Review global candidates"):
            for link in report[:50]:
                src = link.get("source_article_title","?")
                tgt = link.get("target_article_title","?")
                st.markdown(
                    f"- **{src}** → **{tgt}**  \n"
                    f"  anchor: `{link.get('anchor_text','?')}` · "
                    f"sim: `{link.get('similarity_score',0):.2f}`"
                )

    st.markdown("---")
    st.markdown("#### 📦 Manual Export")
    st.caption("If you'd rather run the link engine yourself, export the markdown files:")
    if st.button("Export cluster .md files"):
        try:
            from link_engine_bridge import export_cluster
            folder = export_cluster(cluster_id, rid)
            st.success(f"Exported to: `{folder}`")
            st.code(f"python -m link_engine.cli run {folder}", language="bash")
        except Exception as e:
            st.error(e)