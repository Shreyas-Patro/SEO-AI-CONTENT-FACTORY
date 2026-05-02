"""
Streamlit dashboard component for the research ingestion pipeline.

Drop into dashboard.py with:
    from dashboard_components.ingestion_view import render_ingestion_view
    render_ingestion_view(view_run_id)
"""

import os
import streamlit as st
import tempfile
from pathlib import Path

from ingestion.pipeline import ingest_document
from db.sqlite_ops import get_facts, get_pending_verifications


def render_ingestion_view(view_run_id=None):
    st.markdown("###  Research Ingestion Pipeline")
    st.caption(
        "Upload a research document (Perplexity output, .md/.pdf/.docx) → "
        "we extract → chunk → fact-extract → embed → store in graph."
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader(
            "Drop research document here",
            type=["md", "txt", "pdf", "docx"],
            key=f"ingest_upload_{view_run_id or 'global'}",
        )
    with col2:
        topic_default = ""
        if view_run_id:
            from db.artifacts import get_pipeline_run
            run = get_pipeline_run(view_run_id)
            topic_default = run.get("topic", "") if run else ""
        topic_input = st.text_input("Topic (locality)", value=topic_default,
                                    key=f"ingest_topic_{view_run_id or 'global'}")
        source_url = st.text_input("Source URL (optional)", value="",
                                   key=f"ingest_source_{view_run_id or 'global'}")

    if uploaded and st.button("🔄 Ingest Document", type="primary",
                              key=f"ingest_btn_{view_run_id or 'global'}"):
        # Save to temp file
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=Path(uploaded.name).suffix
        ) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        # Progress bars per stage
        stage_progress = {}
        progress_container = st.container()
        with progress_container:
            stages = ["extract", "chunk", "fact_extract", "plausibility"]
            for s in stages:
                stage_progress[s] = st.progress(0, text=f"{s} (waiting)")

        def cb(stage, pct):
            if stage in stage_progress:
                stage_progress[stage].progress(pct, text=f"{stage} ({pct}%)")

        with st.spinner("Ingesting..."):
            try:
                summary = ingest_document(
                    tmp_path,
                    run_id=view_run_id,
                    topic=topic_input,
                    source_url=source_url,
                    progress_cb=cb,
                )
                st.success(
                    f"✅ Ingested {summary['total_facts']} facts "
                    f"(${summary['total_cost_usd']:.4f})"
                )
                with st.expander("📋 Ingestion summary"):
                    st.json(summary)
            except Exception as e:
                st.error(f"Ingestion failed: {e}")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    st.markdown("---")

    # ─── Knowledge base browser ──────────────────────────────────────────
    st.markdown("#### 🗄️ Knowledge Base (most recent facts)")

    fc1, fc2, fc3 = st.columns(3)
    cat_filter = fc1.selectbox(
        "Category",
        ["all", "property", "legal", "finance", "lifestyle", "infrastructure", "demographic"],
        key="kb_cat",
    )
    loc_filter = fc2.text_input("Location filter", value="", key="kb_loc")
    limit = fc3.slider("Show N most recent", 10, 200, 30, key="kb_limit")

    facts = get_facts(
        category=None if cat_filter == "all" else cat_filter,
        location=loc_filter if loc_filter else None,
        limit=limit,
    )

    if not facts:
        st.info("No facts in knowledge base yet — ingest a document to populate.")
    else:
        st.caption(f"Showing {len(facts)} facts")
        for f in facts[:limit]:
            with st.expander(f"[{f['category']}] {f['content'][:100]}..."):
                st.markdown(f"**Full statement:** {f['content']}")
                st.caption(
                    f"Source: {f.get('source_title', '?')} | "
                    f"Location: {f.get('location', '—')} | "
                    f"Confidence: {f.get('confidence', 0):.2f} | "
                    f"Verified: {'✓' if f.get('verified', 0) else '—'}"
                )

    # ─── Verification queue ──────────────────────────────────────────────
    pending = get_pending_verifications(limit=10)
    if pending:
        st.markdown("---")
        st.markdown(f"#### ⚠️ Verification Queue ({len(pending)} pending)")
        for v in pending:
            with st.expander(f"[{v['issue_type']}] {v['claim_text'][:100]}..."):
                st.write(v["claim_text"])
                if v.get("suggested_correction"):
                    st.caption(f"Suggested: {v['suggested_correction']}")