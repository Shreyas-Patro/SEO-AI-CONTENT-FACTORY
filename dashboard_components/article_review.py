"""Articles tab: list, view, history, scores, manual rerun of writing pipeline."""
import json
import streamlit as st
from db.sqlite_ops import get_articles_by_cluster


def _score_pill(label, value, threshold, fmt="{:.1f}"):
    if value is None or value == 0:
        return f'<span class="score-pill score-warn">{label}: —</span>'
    cls = "score-good" if value >= threshold else "score-bad"
    return f'<span class="score-pill {cls}">{label}: {fmt.format(value)}</span>'


def render_article_review(m):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first."); return

    run = m["get_pipeline_run"](rid)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        st.info("Run Layer 2 first to create articles."); return

    articles = get_articles_by_cluster(cluster_id)
    st.markdown(f"### 📝 Articles ({len(articles)} in cluster)")

    written = sum(1 for a in articles if a.get("status") == "written")
    planned = sum(1 for a in articles if a.get("status") == "planned")
    avg_brand = sum((a.get("brand_tone_score") or 0) for a in articles) / max(written, 1)
    avg_fact = sum((a.get("fact_check_score") or 0) for a in articles) / max(written, 1)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", len(articles))
    c2.metric("Written", written)
    c3.metric("Avg Brand", f"{avg_brand:.1f}/10")
    c4.metric("Avg Fact", f"{avg_fact:.2f}")

    for art in articles:
        icon = {"planned":"📋","written":"✅","published":"🌐"}.get(art["status"], "❔")
        with st.expander(
            f"{icon} [{art['article_type'].upper()}] {art['title']} — {art.get('word_count',0)} words"
        ):
            st.markdown(
                f"**ID:** `{art['id']}` · **Slug:** `/{art['slug']}` · **Status:** {art['status']}"
            )

            pills = (
                _score_pill("Brand", art.get("brand_tone_score") or 0, 7.0)
                + _score_pill("Fact", art.get("fact_check_score") or 0, 0.85, "{:.2f}")
                + _score_pill("Read", art.get("readability_score") or 0, 55, "{:.0f}")
            )
            st.markdown(pills, unsafe_allow_html=True)

            # Action buttons
            ac1, ac2, ac3, ac4 = st.columns(4)
            with ac1:
                if st.button("✍️ Rewrite", key=f"rw_{art['id']}"):
                    try:
                        m["rerun_agent"](rid, "lead_writer", {"article_id": art["id"]})
                        st.rerun()
                    except Exception as e:
                        st.error(e)
            with ac2:
                if st.button("✅ Verify", key=f"fv_{art['id']}"):
                    try:
                        m["rerun_agent"](rid, "fact_verifier", {"article_id": art["id"]})
                        st.rerun()
                    except Exception as e:
                        st.error(e)
            with ac3:
                if st.button("🎨 Audit", key=f"ba_{art['id']}"):
                    try:
                        m["rerun_agent"](rid, "brand_auditor", {"article_id": art["id"]})
                        st.rerun()
                    except Exception as e:
                        st.error(e)
            with ac4:
                if st.button("🏷️ Meta", key=f"mt_{art['id']}"):
                    try:
                        m["rerun_agent"](rid, "meta_tagger", {"article_id": art["id"]})
                        st.rerun()
                    except Exception as e:
                        st.error(e)

            # Content preview
            if art.get("content_md"):
                st.markdown("---")
                with st.expander("📄 Full content"):
                    st.markdown(art["content_md"])
                preview = art["content_md"][:1500]
                st.markdown(preview)
                if len(art["content_md"]) > 1500:
                    st.caption(f"... +{len(art['content_md'])-1500} more chars")

            # Meta tags
            if art.get("meta_title"):
                st.markdown("**Meta Title:** " + art["meta_title"])
                st.markdown("**Meta Description:** " + (art.get("meta_description") or ""))

            # History — the key feature you asked for
            history = json.loads(art.get("history", "[]") or "[]")
            if history:
                st.markdown("**History (chronological changes):**")
                for h in history:
                    st.caption(f"`{h['stage']}` @ {h['timestamp'][:19]} — {h['changes_summary']}")