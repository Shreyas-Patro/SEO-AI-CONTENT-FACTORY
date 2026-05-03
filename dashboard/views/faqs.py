"""FAQ review + delete."""
import json
import streamlit as st
from db.sqlite_ops import get_articles_by_cluster, update_article


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

    total_faqs = 0
    for a in articles:
        try:
            total_faqs += len(json.loads(a.get("faq_json", "[]") or "[]"))
        except Exception:
            pass

    st.markdown(f"### ❓ FAQs ({total_faqs} across {len(articles)} articles)")
    st.caption("Review FAQs and delete those you don't want before writing.")

    for art in articles:
        try:
            faqs = json.loads(art.get("faq_json", "[]") or "[]")
        except Exception:
            faqs = []
        if not faqs:
            continue

        with st.expander(f"📄 {art['title']} — {len(faqs)} FAQs"):
            indices_to_delete = set()
            for i, faq in enumerate(faqs):
                col_chk, col_q = st.columns([1, 12])
                with col_chk:
                    if st.checkbox("X", key=f"del_faq_{art['id']}_{i}",
                                   label_visibility="collapsed"):
                        indices_to_delete.add(i)
                with col_q:
                    st.markdown(f"**Q:** {faq.get('question', '?')}")
                    st.caption(f"**A:** {faq.get('answer', '')[:200]}")

            if indices_to_delete:
                if st.button(f"🗑️ Delete {len(indices_to_delete)} selected FAQs", key=f"delfaqs_{art['id']}"):
                    new_faqs = [f for i, f in enumerate(faqs) if i not in indices_to_delete]
                    update_article(art["id"], faq_json=json.dumps(new_faqs))
                    st.success(f"Deleted {len(indices_to_delete)} FAQs.")
                    st.rerun()