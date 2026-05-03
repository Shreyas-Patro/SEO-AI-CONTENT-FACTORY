"""Articles view: list, inspect, edit, delete."""
import json
import streamlit as st
from db.sqlite_ops import get_articles_by_cluster, update_article, db_conn
from dashboard.auth import is_admin


def _delete_article(article_id):
    """Hard delete — also removes related faqs, history, etc."""
    with db_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        conn.commit()


def _render_planning_stage(articles, run, m):
    """At the planning stage, allow user to delete unwanted articles."""
    st.markdown("### 📋 Planned Articles — Delete the ones you don't want")
    st.caption("This is your last chance to remove articles before writing begins.")

    if "articles_to_delete" not in st.session_state:
        st.session_state["articles_to_delete"] = set()

    for art in articles:
        if art["status"] != "planned":
            continue
        type_emoji = {"hub": "🏛️", "spoke": "📄", "sub_spoke": "📃", "faq": "❓"}.get(
            art.get("article_type", "spoke"), "📄"
        )
        col_chk, col_title = st.columns([1, 10])
        with col_chk:
            checked = st.checkbox(
                "Delete",
                value=art["id"] in st.session_state["articles_to_delete"],
                key=f"chk_del_{art['id']}",
                label_visibility="collapsed",
            )
            if checked:
                st.session_state["articles_to_delete"].add(art["id"])
            else:
                st.session_state["articles_to_delete"].discard(art["id"])
        with col_title:
            st.markdown(f"{type_emoji} **{art['title']}** · `{art['slug']}` · target {art.get('word_count_target', 'N/A')} words")
            outline = json.loads(art.get("outline", "[]") or "[]")
            if outline:
                with st.expander("Outline"):
                    for h in outline[:8]:
                        st.caption(h)

    if st.session_state["articles_to_delete"]:
        st.warning(f"You're about to delete {len(st.session_state['articles_to_delete'])} article(s). This cannot be undone.")
        if st.button("🗑️ Confirm Delete", type="primary"):
            for aid in list(st.session_state["articles_to_delete"]):
                _delete_article(aid)
            st.session_state["articles_to_delete"] = set()
            st.success("Articles deleted.")
            st.rerun()


def _render_written_article(art):
    """Inspect + edit a written article."""
    type_emoji = {"hub": "🏛️", "spoke": "📄", "sub_spoke": "📃", "faq": "❓"}.get(
        art.get("article_type", "spoke"), "📄"
    )

    title_line = f"{type_emoji} **{art['title']}** — {art.get('word_count', 0)} words"
    with st.expander(title_line, expanded=False):
        cols = st.columns(4)
        cols[0].metric("Brand", f"{art.get('brand_tone_score') or 0:.1f}/10")
        cols[1].metric("Fact", f"{art.get('fact_check_score') or 0:.2f}")
        cols[2].metric("Read", f"{art.get('readability_score') or 0:.0f}")
        cols[3].metric("Status", art.get("status", "?"))

        edit_key = f"edit_article_{art['id']}"
        view_tabs = st.tabs(["📖 Read", "✏️ Edit", "🗂️ Meta", "🕒 History", "🗑️ Delete"])

        with view_tabs[0]:
            st.markdown(art.get("content_md", "_no content_"))

        with view_tabs[1]:
            edited = st.text_area(
                "Markdown content (edit + save):",
                value=art.get("content_md", ""),
                height=600,
                key=f"ta_{edit_key}",
            )
            wc = len(edited.split())
            st.caption(f"Word count: {wc}")
            if st.button("💾 Save edits", key=f"save_{edit_key}"):
                update_article(
                    art["id"],
                    content_md=edited,
                    word_count=wc,
                )
                st.success("Saved.")
                st.rerun()

        with view_tabs[2]:
            if art.get("meta_title"):
                st.markdown(f"**Meta title:** {art['meta_title']}")
            if art.get("meta_description"):
                st.markdown(f"**Meta description:** {art['meta_description']}")
            schema = art.get("schema_json")
            if schema and schema != "{}":
                try:
                    st.json(json.loads(schema))
                except Exception:
                    st.code(schema[:2000])

        with view_tabs[3]:
            history = json.loads(art.get("history", "[]") or "[]")
            for h in history:
                st.caption(f"`{h['stage']}` @ {h['timestamp'][:19]} — {h['changes_summary']}")

        with view_tabs[4]:
            st.warning("Deletion is permanent.")
            if st.button("🗑️ Delete this article", key=f"delart_{art['id']}", type="secondary"):
                _delete_article(art["id"])
                st.success("Deleted.")
                st.rerun()


def render(m):
    rid = st.session_state.viewing_run_id
    if not rid:
        st.info("Open a run first.")
        return

    run = m["get_pipeline_run"](rid)
    cluster_id = run.get("cluster_id") if run else None
    if not cluster_id:
        st.info("Run Layer 2 first to create articles.")
        return

    articles = get_articles_by_cluster(cluster_id)
    if not articles:
        st.info("No articles yet.")
        return

    st.markdown(f"### 📝 Articles ({len(articles)} in cluster)")

    written = sum(1 for a in articles if a.get("status") == "written")
    planned = sum(1 for a in articles if a.get("status") == "planned")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total", len(articles))
    c2.metric("Written", written)
    c3.metric("Planned", planned)

    if planned > 0:
        _render_planning_stage(articles, run, m)
        st.markdown("---")

    if written > 0:
        st.markdown("### ✅ Written Articles")
        for art in articles:
            if art["status"] != "written":
                continue
            _render_written_article(art)