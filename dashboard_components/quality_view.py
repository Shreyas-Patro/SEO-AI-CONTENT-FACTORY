"""Quality tab: per-article quality loop visualization with iteration counts."""
import json
import streamlit as st
from db.sqlite_ops import get_articles_by_cluster


def render_quality_view(m):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first."); return

    run = m["get_pipeline_run"](rid)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        st.info("No cluster yet."); return

    articles = get_articles_by_cluster(cluster_id)
    written = [a for a in articles if a.get("content_md")]
    if not written:
        st.info("No written articles yet."); return

    st.markdown(f"### 🔄 Quality Loop — {len(written)} articles")
    st.caption("Each row is one article's journey through the writer → verify → audit → (rewrite) → meta loop.")

    for art in written:
        history = json.loads(art.get("history", "[]") or "[]")
        stages = [h["stage"] for h in history]
        rewrite_count = sum(1 for s in stages if s == "rewriter")
        write_count = sum(1 for s in stages if s == "lead_writer")

        cols = st.columns([3, 1, 1, 1, 1])
        cols[0].markdown(f"**{art['title'][:60]}**")
        cols[0].caption(f"`{art['id']}` · {art.get('word_count',0)} words")

        brand = art.get("brand_tone_score") or 0
        fact = art.get("fact_check_score") or 0
        read = art.get("readability_score") or 0
        cols[1].metric("Brand", f"{brand:.1f}", delta="ok" if brand >= 7.0 else "low")
        cols[2].metric("Fact", f"{fact:.2f}", delta="ok" if fact >= 0.85 else "low")
        cols[3].metric("Read", f"{read:.0f}", delta="ok" if read >= 55 else "low")
        cols[4].markdown(
            f'<span class="iter-badge">writes: {write_count} · rewrites: {rewrite_count}</span>',
            unsafe_allow_html=True
        )

        if history:
            with st.expander(f"Show timeline ({len(history)} steps)"):
                for h in history:
                    st.caption(f"`{h['stage']}` @ {h['timestamp'][:19]} — {h['changes_summary']}")