"""Interlinking — proper bridge between pipeline + link engine."""
import json
import os
from pathlib import Path
import streamlit as st
from db.sqlite_ops import get_articles_by_cluster


def _export_cluster_to_disk(cluster_id, run_id):
    """Write all written articles to outputs/interlink_export_<run_id>/."""
    articles = get_articles_by_cluster(cluster_id)
    written = [a for a in articles if (a.get("content_md") or "").strip()]

    export_dir = Path("outputs") / f"interlink_export_{run_id[-8:]}"
    export_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for art in written:
        slug = art.get("slug", "untitled")
        kw = art.get("target_keywords", "{}") or "{}"
        try:
            kw_obj = json.loads(kw)
            primary = kw_obj.get("primary", "") if isinstance(kw_obj, dict) else ""
        except Exception:
            primary = ""

        frontmatter = (
            f"---\n"
            f'title: "{art["title"]}"\n'
            f"slug: \"{slug}\"\n"
            f"url: \"/{slug}\"\n"
            f"primary_keyword: \"{primary}\"\n"
            f"---\n\n"
        )
        filepath = export_dir / f"{slug}.md"
        filepath.write_text(frontmatter + (art.get("content_md") or ""), encoding="utf-8")
        count += 1

    return export_dir, count


def _load_into_link_engine(export_dir):
    """Run the link engine pipeline on the exported folder."""
    from link_engine.db.session import get_session_factory
    from link_engine.stages.article_ops import process_directory

    factory = get_session_factory()
    session = factory()
    try:
        result = process_directory(Path(export_dir), session)
        return result
    finally:
        session.close()


def render(m):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
        return

    run = m["get_pipeline_run"](rid)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        st.info("Run Layer 2 first.")
        return

    articles = get_articles_by_cluster(cluster_id)
    written = [a for a in articles if (a.get("content_md") or "").strip()]
    if not written:
        st.warning("No written articles yet — finish Layer 3 first.")
        return

    st.markdown("### 🔗 Interlinking — Cluster Pass")
    st.metric("Articles ready", len(written))

    cs1, cs2 = st.columns(2)

    # Option 1: Run interlinking inline
    with cs1:
        st.markdown("#### Option A: Run interlinking now")
        if st.button("🔗 Run cluster interlinking", type="primary", key="run_inline_link"):
            with st.spinner("Exporting articles + running link engine..."):
                export_dir, count = _export_cluster_to_disk(cluster_id, rid)
                st.session_state[f"export_dir_{rid}"] = str(export_dir)
                st.success(f"Exported {count} articles to `{export_dir}`")

                result = _load_into_link_engine(export_dir)
                st.session_state[f"link_result_{rid}"] = result
                st.success(
                    f"Link engine: {result.get('matches_found',0)} matches, "
                    f"{result.get('anchors_passed',0)} passed, "
                    f"{result.get('anchors_errored',0)} rejected"
                )
                st.rerun()

    # Option 2: Open external dashboard
    with cs2:
        st.markdown("#### Option B: Open Link Engine dashboard")
        if st.button("🚀 Export + Open Link Engine UI", key="export_open_le"):
            export_dir, count = _export_cluster_to_disk(cluster_id, rid)
            st.session_state[f"export_dir_{rid}"] = str(export_dir)
            st.success(f"✓ Exported {count} articles to `{export_dir}`")
            # Run pipeline first so articles are loaded for review
            with st.spinner("Loading into link engine for review..."):
                result = _load_into_link_engine(export_dir)
                st.session_state[f"link_result_{rid}"] = result
            st.markdown("**Now run this in a new terminal:**")
            st.code("python -m link_engine.cli dashboard", language="bash")
            st.caption("The link engine dashboard will show all candidates ready for review.")

    # Show results
    result = st.session_state.get(f"link_result_{rid}")
    if result:
        st.markdown("---")
        st.markdown("#### Last Run Results")
        cols = st.columns(4)
        cols[0].metric("Matches found", result.get("matches_found", 0))
        cols[1].metric("Anchors passed", result.get("anchors_passed", 0))
        cols[2].metric("Anchors errored", result.get("anchors_errored", 0))
        cols[3].metric("Chunks", result.get("chunks", 0))

        st.info(
            "Open the link engine dashboard to review and approve each link, "
            "then inject them back into your articles."
        )