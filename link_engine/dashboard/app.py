import re
import shutil
from datetime import datetime
from pathlib import Path

import frontmatter as fm
import streamlit as st

st.set_page_config(
    page_title="Canvas Homes Link Engine",
    page_icon="🔗",
    layout="wide",
)

from link_engine.config import get_config
from link_engine.db.models import Anchor, Article, Error, Injection, Match, Run
from link_engine.reports.reporter import write_reports
from link_engine.stages.article_ops import (
    delete_article,
    process_directory,
    process_single_article,
    reprocess_all,
    split_multi_article_paste,
)
from link_engine.stages.inject import inject_approved_links


def get_db():
    from link_engine.db.session import get_session_factory
    factory = get_session_factory()
    return factory()


def make_url(slug: str) -> str:
    return f"https://canvas-homes.com/blogs/{slug}"


def slugify(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


CONTENT_DIR = Path("test_posts")
CONTENT_DIR.mkdir(parents=True, exist_ok=True)


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("🔗 Canvas Homes Link Engine")
tab_name = st.sidebar.radio(
    "Navigate",
    [
        "Add Article",
        "Bulk Upload",
        "Reprocess All",
        "Review Queue",
        "All Articles",
        "Inject Approved",
        "Injected Posts",
        "Errors",
        "Run History",
    ],
    index=0,
)
st.sidebar.markdown("---")
st.sidebar.caption("Semantic internal linking — human approved before injection.")


# ── Add Article (single) ─────────────────────────────────────────────────────
if tab_name == "Add Article":
    st.title("Add Article")
    st.caption("Add one article at a time. Use Bulk Upload for 10+ articles.")

    input_mode = st.radio("Input method", ["Paste markdown", "Upload .md file"], horizontal=True)

    with st.form("add_article_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        title = c1.text_input("Title", placeholder="How to Buy a Car")
        slug_input = c2.text_input("Slug (optional)", placeholder="auto-generated from title")

        c3, c4 = st.columns(2)
        url_input = c3.text_input("URL (optional)", placeholder="auto-generated from slug")
        tags_input = c4.text_input("Tags (comma-separated)", placeholder="cars, buying, guide")

        if input_mode == "Paste markdown":
            body = st.text_area(
                "Body (markdown — no frontmatter)",
                height=400,
                placeholder="## What is HSR Layout\n\nHSR Layout, which stands for...",
            )
            uploaded_file = None
        else:
            uploaded_file = st.file_uploader("Upload .md file", type=["md"])
            body = None

        submitted = st.form_submit_button("Add and process", type="primary")

    if submitted:
        if not title:
            st.error("Title is required.")
            st.stop()
        if input_mode == "Paste markdown" and not body:
            st.error("Body is required.")
            st.stop()
        if input_mode == "Upload .md file" and uploaded_file is None:
            st.error("Please select a .md file.")
            st.stop()

        slug = slug_input.strip() or slugify(title)
        url = url_input.strip() or make_url(slug)
        tags = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input else []

        if input_mode == "Upload .md file":
            raw_upload = uploaded_file.read().decode("utf-8")
            try:
                parsed = fm.loads(raw_upload)
                meta = dict(parsed.metadata)
                meta["title"] = title
                meta["slug"] = slug
                meta["url"] = url
                if tags:
                    meta["tags"] = tags
                body = parsed.content
            except Exception:
                body = raw_upload
                meta = {"title": title, "slug": slug, "url": url}
                if tags:
                    meta["tags"] = tags
        else:
            meta = {"title": title, "slug": slug, "url": url}
            if tags:
                meta["tags"] = tags

        file_path = CONTENT_DIR / f"{slug}.md"
        if file_path.exists():
            st.warning(f"A file with slug `{slug}` already exists. It will be overwritten.")
        post = fm.Post(body, **meta)
        file_path.write_text(fm.dumps(post), encoding="utf-8")

        with st.spinner("Processing (ingest → extract → chunk → embed → match → score)..."):
            session = get_db()
            try:
                summary = process_single_article(file_path, session)
            finally:
                session.close()

        st.success(f"Processed: **{summary['article']}** ({summary['status']})")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Chunks", summary["chunks"])
        c2.metric("New embeddings", summary["new_embeddings"])
        c3.metric("Matches found", summary["matches_found"])
        c4.metric("Passed confidence", summary["anchors_passed"])

        if summary["anchors_errored"]:
            st.warning(f"{summary['anchors_errored']} match(es) rejected by confidence gate.")
        if summary["matches_found"] > 0:
            st.info("Go to **Review Queue** to approve the proposed links.")
        else:
            st.info("No matches found involving this article.")


# ── Bulk Upload (files or paste) ─────────────────────────────────────────────
elif tab_name == "Bulk Upload":
    st.title("Bulk Upload")
    st.caption(
        "Add many articles at once. Unchanged articles are skipped. "
        "Matching runs across the whole corpus, but only NEW matches cost LLM money."
    )

    bulk_mode = st.radio(
        "Input method",
        ["Upload .md files", "Paste multiple articles"],
        horizontal=True,
    )

    # ─── FILE UPLOAD MODE ───
    if bulk_mode == "Upload .md files":
        uploaded_files = st.file_uploader(
            "Upload .md files",
            type=["md"],
            accept_multiple_files=True,
            help="Files should have frontmatter with at least `title` and `slug`.",
        )

        if uploaded_files:
            st.success(f"{len(uploaded_files)} file(s) selected.")

            preview_rows = []
            for f in uploaded_files:
                raw = f.read().decode("utf-8")
                f.seek(0)
                try:
                    parsed = fm.loads(raw)
                    t = parsed.metadata.get("title", f.name.replace(".md", ""))
                    s = parsed.metadata.get("slug") or slugify(t)
                    words = len(parsed.content.split())
                except Exception:
                    t = f.name.replace(".md", "")
                    s = slugify(t)
                    words = len(raw.split())
                preview_rows.append({"filename": f.name, "title": t, "slug": s, "word_count": words})

            st.markdown("**Preview:**")
            st.dataframe(preview_rows, use_container_width=True, hide_index=True)

            n_new = len(uploaded_files)
            est_extract = n_new * 0.0075
            est_score_lo = n_new * 3 * 0.0036
            est_score_hi = n_new * 10 * 0.0036
            st.info(
                f"💰 Estimated cost: ${est_extract + est_score_lo:.2f} – "
                f"${est_extract + est_score_hi:.2f}  \n"
                f"(Extraction: ${est_extract:.2f} · scoring varies with how many matches are found.)"
            )

            overwrite_existing = st.checkbox("Overwrite existing articles with the same slug", value=True)

            if st.button("Upload and process batch", type="primary"):
                session = get_db()
                try:
                    existing_slugs = {a[0] for a in session.query(Article.slug).all()}
                finally:
                    session.close()

                written, skipped = [], []
                for f in uploaded_files:
                    raw = f.read().decode("utf-8")
                    try:
                        parsed = fm.loads(raw)
                        t = parsed.metadata.get("title", f.name.replace(".md", ""))
                        s = parsed.metadata.get("slug") or slugify(t)
                        body = parsed.content
                        meta = dict(parsed.metadata)
                    except Exception:
                        t = f.name.replace(".md", "")
                        s = slugify(t)
                        body = raw
                        meta = {}

                    if s in existing_slugs and not overwrite_existing:
                        skipped.append(f.name)
                        continue

                    meta["title"] = t
                    meta["slug"] = s
                    if "url" not in meta:
                        meta["url"] = make_url(s)

                    (CONTENT_DIR / f"{s}.md").write_text(fm.dumps(fm.Post(body, **meta)), encoding="utf-8")
                    written.append(f.name)

                if skipped:
                    st.warning(f"Skipped {len(skipped)} (existing slug, overwrite off).")
                if not written:
                    st.error("No files were written.")
                    st.stop()

                st.info(f"Wrote {len(written)} file(s). Running pipeline...")
                progress_bar = st.progress(0.0)
                status_text = st.empty()

                def update_progress(msg, frac):
                    progress_bar.progress(frac)
                    status_text.text(msg)

                session = get_db()
                try:
                    summary = process_directory(CONTENT_DIR, session, progress_callback=update_progress)
                finally:
                    session.close()

                st.success("Batch complete!")
                c1, c2, c3 = st.columns(3)
                c1.metric("New", summary["new"])
                c2.metric("Changed", summary["changed"])
                c3.metric("Unchanged (skipped)", summary["unchanged"])

                c4, c5, c6, c7 = st.columns(4)
                c4.metric("Chunks", summary["chunks"])
                c5.metric("Embeddings", summary["new_embeddings"])
                c6.metric("Matches found", summary["matches_found"])
                c7.metric("Passed confidence", summary["anchors_passed"])

                if summary["matches_found"] > 0:
                    st.info("Pending links available for review in the **Review Queue** tab.")

    # ─── BULK PASTE MODE ───
    else:
        st.markdown(
            "**Paste one or more articles.** Each article must be a full markdown document "
            "with YAML frontmatter (`---\\ntitle: ...\\nslug: ...\\n---`). "
            "Concatenate multiple articles back-to-back — the parser finds each `---` pair."
        )

        with st.expander("Example format", expanded=False):
            st.code(
                "---\n"
                "title: What is an SUV\n"
                "slug: what-is-an-suv\n"
                "---\n\n"
                "## The Rise of the SUV\n\n"
                "The SUV has become the default...\n\n"
                "---\n"
                "title: What is a Sedan\n"
                "slug: what-is-a-sedan\n"
                "---\n\n"
                "## The Classic Passenger Car\n\n"
                "A sedan is...\n",
                language="markdown",
            )

        pasted = st.text_area(
            "Paste articles here",
            height=500,
            placeholder="---\ntitle: ...\nslug: ...\n---\n\n## Body\n\n...",
        )

        if pasted.strip():
            articles = split_multi_article_paste(pasted)
            if not articles:
                st.warning("No articles detected. Make sure each starts with `---` frontmatter and has a `title` field.")
            else:
                st.success(f"Detected **{len(articles)}** article(s).")

                preview_rows = [
                    {
                        "title": a["title"],
                        "slug": a["slug"] or slugify(a["title"]),
                        "word_count": len(a["body"].split()),
                    }
                    for a in articles
                ]
                st.dataframe(preview_rows, use_container_width=True, hide_index=True)

                n = len(articles)
                est_extract = n * 0.0075
                est_score_lo = n * 3 * 0.0036
                est_score_hi = n * 10 * 0.0036
                st.info(
                    f"💰 Estimated cost: ${est_extract + est_score_lo:.2f} – "
                    f"${est_extract + est_score_hi:.2f}"
                )

                overwrite_existing = st.checkbox(
                    "Overwrite existing articles with the same slug",
                    value=True,
                    key="bulk_paste_overwrite",
                )

                if st.button("Process pasted articles", type="primary"):
                    session = get_db()
                    try:
                        existing_slugs = {a[0] for a in session.query(Article.slug).all()}
                    finally:
                        session.close()

                    written, skipped = [], []
                    for a in articles:
                        s = a["slug"] or slugify(a["title"])
                        if s in existing_slugs and not overwrite_existing:
                            skipped.append(a["title"])
                            continue

                        meta = dict(a["metadata"])
                        meta["title"] = a["title"]
                        meta["slug"] = s
                        if "url" not in meta:
                            meta["url"] = make_url(s)

                        (CONTENT_DIR / f"{s}.md").write_text(
                            fm.dumps(fm.Post(a["body"], **meta)), encoding="utf-8"
                        )
                        written.append(a["title"])

                    if skipped:
                        st.warning(f"Skipped {len(skipped)} (existing slug, overwrite off).")
                    if not written:
                        st.error("Nothing to process.")
                        st.stop()

                    st.info(f"Wrote {len(written)} file(s). Running pipeline...")
                    progress_bar = st.progress(0.0)
                    status_text = st.empty()

                    def update_progress(msg, frac):
                        progress_bar.progress(frac)
                        status_text.text(msg)

                    session = get_db()
                    try:
                        summary = process_directory(CONTENT_DIR, session, progress_callback=update_progress)
                    finally:
                        session.close()

                    st.success("Batch complete!")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("New", summary["new"])
                    c2.metric("Changed", summary["changed"])
                    c3.metric("Unchanged", summary["unchanged"])

                    c4, c5, c6, c7 = st.columns(4)
                    c4.metric("Chunks", summary["chunks"])
                    c5.metric("Embeddings", summary["new_embeddings"])
                    c6.metric("Matches", summary["matches_found"])
                    c7.metric("Passed confidence", summary["anchors_passed"])

                    if summary["matches_found"] > 0:
                        st.info("Pending links available in the **Review Queue** tab.")


# ── Reprocess All ────────────────────────────────────────────────────────────
elif tab_name == "Reprocess All":
    st.title("Reprocess All")
    st.caption(
        "Rebuild the pipeline for your entire corpus. Use this after changing the LLM model, "
        "the embedding model, or when you want to start clean without losing the articles."
    )

    session = get_db()
    total_articles = session.query(Article).count()
    total_matches = session.query(Match).count()
    session.close()

    c1, c2 = st.columns(2)
    c1.metric("Articles in DB", total_articles)
    c2.metric("Matches in DB", total_matches)

    if total_articles == 0:
        st.info("No articles in the DB yet. Add some first.")
        st.stop()

    st.markdown("---")
    st.markdown("**What to invalidate:**")

    invalidate_phrases = st.checkbox(
        "Re-extract phrases (LLM call per article)",
        value=False,
        help=f"Costs approximately ${total_articles * 0.0075:.2f} for {total_articles} articles.",
    )
    invalidate_embeddings = st.checkbox(
        "Recompute embeddings (local, free)",
        value=False,
        help="No LLM cost — runs on your CPU. Use if you changed the embedding model.",
    )
    invalidate_confidence = st.checkbox(
        "Re-score confidence (LLM call per match)",
        value=False,
        help=f"Approximate cost depends on how many matches are found. Scales with article count.",
    )

    # Cost estimate
    est_cost = 0.0
    if invalidate_phrases:
        est_cost += total_articles * 0.0075
    if invalidate_confidence:
        # rough estimate: ~5 matches per article on average
        est_cost += total_articles * 5 * 0.0036

    if est_cost > 0:
        st.warning(f"💰 **Estimated LLM cost for this operation: ~${est_cost:.2f}**")
    elif invalidate_embeddings:
        st.info("💰 Local embedding recompute only — no LLM cost.")

    st.markdown("---")
    confirmed = st.text_input(
        "Type REPROCESS to confirm",
        placeholder="REPROCESS",
        help="This is a safety check — type the word exactly to enable the button.",
    )

    can_run = confirmed.strip() == "REPROCESS" and (
        invalidate_phrases or invalidate_embeddings or invalidate_confidence
    )

    if not (invalidate_phrases or invalidate_embeddings or invalidate_confidence):
        st.caption("Select at least one invalidation option to proceed.")

    if st.button("Run reprocess", type="primary", disabled=not can_run):
        progress_bar = st.progress(0.0)
        status_text = st.empty()

        def update_progress(msg, frac):
            progress_bar.progress(frac)
            status_text.text(msg)

        session = get_db()
        try:
            summary = reprocess_all(
                CONTENT_DIR,
                session,
                invalidate_phrases=invalidate_phrases,
                invalidate_embeddings=invalidate_embeddings,
                invalidate_confidence=invalidate_confidence,
                progress_callback=update_progress,
            )
        finally:
            session.close()

        st.success("Reprocess complete.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Articles", summary["new"] + summary["changed"] + summary["unchanged"])
        c2.metric("Matches", summary["matches_found"])
        c3.metric("Passed confidence", summary["anchors_passed"])


# ── Review Queue ──────────────────────────────────────────────────────────────
elif tab_name == "Review Queue":
    st.title("Review Queue")
    session = get_db()

    pending = (
        session.query(Anchor)
        .filter(Anchor.status == "pending_review")
        .join(Anchor.match)
        .order_by(Match.similarity_score.desc())
        .all()
    )
    approved_count = session.query(Anchor).filter(Anchor.status == "approved").count()
    rejected_count = session.query(Anchor).filter(Anchor.status == "rejected").count()

    c1, c2, c3 = st.columns(3)
    c1.metric("Pending review", len(pending))
    c2.metric("Approved", approved_count)
    c3.metric("Rejected", rejected_count)

    if not pending:
        st.success("No pending links.")
        session.close()
        st.stop()

    st.markdown("---")
    col_a, col_r, _ = st.columns([1, 1, 5])
    if col_a.button("Approve all", type="primary"):
        for a in pending:
            a.status = "approved"
        session.commit()
        st.rerun()
    if col_r.button("Reject all"):
        for a in pending:
            a.status = "rejected"
        session.commit()
        st.rerun()

    score_min = st.slider("Min similarity score", 0.0, 1.0, 0.0, 0.05)
    filtered = [a for a in pending if a.match.similarity_score >= score_min]
    st.markdown(f"**{len(filtered)} links to review**")
    st.markdown("---")

    for anchor in filtered:
        match = anchor.match
        source = match.source_chunk
        target = match.target_chunk
        score = match.similarity_score
        confidence = anchor.llm_confidence or 0

        target_url = make_url(target.article.slug)
        target_section = slugify(target.heading or "")
        full_link = f"{target_url}#{target_section}" if target_section else target_url

        icon = "🟢" if score >= 0.80 else "🟡" if score >= 0.65 else "🔴"

        with st.expander(
            f"{icon} **{source.article.title}** → **{target.article.title}** "
            f"| Score: {score:.2f} | Confidence: {confidence}/5",
            expanded=False,
        ):
            if anchor.match.matched_title_phrase:
                st.info(f"📌 Matched on title phrase: **\"{anchor.match.matched_title_phrase}\"**")

            left, right = st.columns(2)
            with left:
                st.markdown("**Source passage**")
                text = source.text
                phrase = anchor.anchor_text or ""
                if phrase and phrase in text:
                    highlighted = text.replace(phrase, f"**`{phrase}`**", 1)
                else:
                    highlighted = text
                st.markdown(highlighted)
            with right:
                st.markdown(f"**Target section:** `{target.heading or 'Introduction'}`")
                st.markdown(f"**Target URL:** `{full_link}`")
                st.markdown("---")
                st.markdown(target.text)

            st.markdown("---")
            st.markdown(f"*LLM reasoning: {anchor.reasoning or 'N/A'}*")

            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            new_text = c1.text_input(
                "Anchor text",
                value=anchor.edited_anchor or anchor.anchor_text or "",
                key=f"edit_{anchor.anchor_id}",
                label_visibility="collapsed",
            )
            if c2.button("Approve", key=f"app_{anchor.anchor_id}", type="primary"):
                anchor.status = "approved"
                if new_text and new_text != anchor.anchor_text:
                    anchor.edited_anchor = new_text
                session.commit()
                st.rerun()
            if c3.button("Reject", key=f"rej_{anchor.anchor_id}"):
                anchor.status = "rejected"
                session.commit()
                st.rerun()
            if c4.button("Save edit", key=f"sav_{anchor.anchor_id}"):
                anchor.edited_anchor = new_text
                anchor.status = "approved"
                session.commit()
                st.rerun()

    session.close()


# ── All Articles ──────────────────────────────────────────────────────────────
elif tab_name == "All Articles":
    st.title("All Articles")
    session = get_db()

    articles = session.query(Article).order_by(Article.title).all()

    if not articles:
        st.info("No articles ingested yet.")
        session.close()
        st.stop()

    st.metric("Total articles", len(articles))
    st.markdown("---")

    search = st.text_input("Search articles", placeholder="Filter by title or slug...")
    filtered = [
        a for a in articles
        if not search or search.lower() in a.title.lower() or search.lower() in a.slug.lower()
    ]

    for article in filtered:
        chunk_count = len(article.chunks)
        injected_count = sum(
            1 for chunk in article.chunks
            for match in chunk.source_matches
            if match.anchor and match.anchor.injection and match.anchor.injection.status == "injected"
        )

        with st.expander(
            f"**{article.title}**  |  {chunk_count} chunks  |  {injected_count} links injected",
            expanded=False,
        ):
            col1, col2 = st.columns(2)
            col1.markdown(f"**Slug:** `{article.slug}`")
            col1.markdown(f"**URL:** [{make_url(article.slug)}]({make_url(article.slug)})")
            col2.markdown(f"**Status:** `{article.status}`")
            col2.markdown(f"**File:** `{Path(article.file_path).name}`")

            st.markdown("---")
            st.markdown("**Sections:**")
            for chunk in sorted(article.chunks, key=lambda c: c.position_index or 0):
                st.markdown(f"- `{chunk.heading or 'Introduction'}` — {chunk.word_count} words")

            st.markdown("---")
            st.markdown("**Content:**")
            file_path = Path(article.file_path)
            if file_path.exists():
                raw = file_path.read_text(encoding="utf-8")
                post = fm.loads(raw)
                st.markdown(post.content)

            st.markdown("---")
            del_key = f"del_confirm_{article.article_id}"
            if del_key not in st.session_state:
                st.session_state[del_key] = False

            if not st.session_state[del_key]:
                if st.button("🗑️ Delete article", key=f"del_{article.article_id}"):
                    st.session_state[del_key] = True
                    st.rerun()
            else:
                st.warning(f"This will delete **{article.title}** and all its data.")
                col_d1, col_d2, col_d3 = st.columns([1, 1, 4])
                also_delete_file = col_d3.checkbox(
                    "Also delete .md file from disk",
                    key=f"del_file_{article.article_id}",
                )
                if col_d1.button("Confirm delete", key=f"del_btn_{article.article_id}", type="primary"):
                    file_to_delete = Path(article.file_path) if also_delete_file else None
                    title_copy = article.title
                    delete_article(article.article_id, session)
                    if file_to_delete and file_to_delete.exists():
                        file_to_delete.unlink()
                    st.session_state[del_key] = False
                    st.success(f"Deleted: {title_copy}")
                    st.rerun()
                if col_d2.button("Cancel", key=f"del_cancel_{article.article_id}"):
                    st.session_state[del_key] = False
                    st.rerun()

    session.close()


# ── Inject Approved ───────────────────────────────────────────────────────────
elif tab_name == "Inject Approved":
    st.title("Inject Approved Links")
    session = get_db()

    approved = (
        session.query(Anchor)
        .filter(Anchor.status == "approved")
        .filter(~Anchor.anchor_id.in_(session.query(Injection.anchor_id)))
        .all()
    )

    st.metric("Approved links ready to inject", len(approved))

    if not approved:
        st.info("No approved links pending injection.")
        session.close()
        st.stop()

    st.markdown("**Links to be injected:**")
    for anchor in approved:
        phrase = anchor.edited_anchor or anchor.anchor_text
        src_title = anchor.match.source_chunk.article.title
        tgt_title = anchor.match.target_chunk.article.title
        st.markdown(f"- `{phrase}` — **{src_title}** → **{tgt_title}**")

    st.markdown("---")
    dry_run = st.checkbox("Dry run (preview only — do not modify files)", value=True)

    if st.button("Inject links", type="primary"):
        run = Run(articles_processed=0)
        session.add(run)
        session.flush()
        with st.spinner("Injecting..."):
            results = inject_approved_links(approved, session, run.run_id, dry_run=dry_run)
            session.commit()
        st.success(
            f"Done! Injected: {results['injected']}  |  "
            f"Errors: {results['errors']}  |  Skipped: {results['skipped']}"
        )
        if not dry_run:
            write_reports(run.run_id, session)
            st.info("Reports written to ./output/")

    session.close()


# ── Injected Posts ────────────────────────────────────────────────────────────
elif tab_name == "Injected Posts":
    st.title("Injected Posts")
    session = get_db()

    injections = (
        session.query(Injection)
        .filter_by(status="injected")
        .order_by(Injection.injected_at.desc())
        .all()
    )

    if not injections:
        st.info("No injected links yet.")
        session.close()
        st.stop()

    by_article = {}
    for inj in injections:
        article = inj.anchor.match.source_chunk.article
        by_article.setdefault(article.article_id, {"article": article, "injections": []})
        by_article[article.article_id]["injections"].append(inj)

    c1, c2 = st.columns(2)
    c1.metric("Modified articles", len(by_article))
    c2.metric("Total links injected", len(injections))
    st.markdown("---")

    for aid, data in by_article.items():
        article = data["article"]
        article_injections = data["injections"]
        article_url = make_url(article.slug)

        with st.expander(
            f"**{article.title}**  —  {len(article_injections)} link(s) injected",
            expanded=True,
        ):
            st.markdown(f"**URL:** [{article_url}]({article_url})")

            file_path = Path(article.file_path)
            if file_path.exists():
                raw = file_path.read_text(encoding="utf-8")
                col_d1, col_d2 = st.columns([1, 5])
                col_d1.download_button(
                    label="⬇️ Download .md",
                    data=raw,
                    file_name=f"{article.slug}.md",
                    mime="text/markdown",
                    key=f"dl_{aid}",
                )
                col_d2.caption(f"File: `{file_path.name}` ({len(raw):,} characters)")
            else:
                st.warning(f"File not found: {file_path}")
                continue

            st.markdown("---")
            st.markdown("**Injected links:**")
            for inj in article_injections:
                anchor = inj.anchor
                match = anchor.match
                phrase = anchor.edited_anchor or anchor.anchor_text
                tgt_title = match.target_chunk.article.title
                tgt_slug = match.target_chunk.article.slug
                tgt_url = make_url(tgt_slug)
                injected_at = inj.injected_at.strftime("%Y-%m-%d %H:%M") if inj.injected_at else "—"

                c1, c2, c3 = st.columns([3, 4, 2])
                c1.markdown(f"`{phrase}`")
                c2.markdown(f"→ [{tgt_title}]({tgt_url})")
                c3.caption(injected_at)

            st.markdown("---")
            view = st.radio(
                "View", ["Rendered (with live links)", "Raw markdown"],
                key=f"view_{aid}", horizontal=True,
            )

            if view == "Raw markdown":
                display = raw
                for inj in article_injections:
                    anchor = inj.anchor
                    phrase = anchor.edited_anchor or anchor.anchor_text
                    tgt_slug = anchor.match.target_chunk.article.slug
                    tgt_url = make_url(tgt_slug)
                    link_md = f"[{phrase}]({tgt_url})"
                    display = display.replace(link_md, f">>> {link_md} <<<")
                st.code(display, language="markdown")
            else:
                post = fm.loads(raw)
                meta = dict(post.metadata)
                if meta:
                    tags = meta.get("tags", [])
                    tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
                    st.caption(
                        f"Title: {meta.get('title', '—')}  |  URL: {article_url}  |  Tags: {tags_str}"
                    )
                st.markdown(post.content, unsafe_allow_html=False)

            st.markdown("---")
            backup_path = article_injections[0].backup_path
            if backup_path and Path(backup_path).exists():
                st.caption(f"Backup: `{backup_path}`")
                if st.button("Restore original", key=f"restore_{aid}"):
                    shutil.copy2(backup_path, file_path)
                    for inj in article_injections:
                        inj.status = "skipped"
                        inj.error_message = "Manually restored"
                    session.commit()
                    st.success(f"Restored {article.title}.")
                    st.rerun()

    session.close()


# ── Errors ────────────────────────────────────────────────────────────────────
elif tab_name == "Errors":
    st.title("Errors")
    session = get_db()

    errors = session.query(Error).filter(Error.resolved_at.is_(None)).all()

    if not errors:
        st.success("No unresolved errors.")
        session.close()
        st.stop()

    st.metric("Unresolved errors", len(errors))
    st.markdown("---")

    for err in errors:
        with st.expander(
            f"[{err.stage.upper()}] {err.error_type} — {err.message[:80]}",
            expanded=False,
        ):
            st.markdown(f"**Stage:** `{err.stage}`")
            st.markdown(f"**Type:** `{err.error_type}`")
            st.markdown(f"**Message:** {err.message}")
            if err.article_id:
                st.markdown(f"**Article:** `{err.article_id}`")
            st.caption(f"Error ID: {err.error_id} | {err.created_at}")

    session.close()


# ── Run History ───────────────────────────────────────────────────────────────
elif tab_name == "Run History":
    st.title("Run History")
    session = get_db()

    runs = session.query(Run).order_by(Run.started_at.desc()).limit(20).all()

    if not runs:
        st.info("No runs yet.")
        session.close()
        st.stop()

    for run in runs:
        duration = "In progress"
        if run.completed_at and run.started_at:
            secs = (run.completed_at - run.started_at).total_seconds()
            duration = f"{secs:.1f}s"

        with st.expander(
            f"Run {run.run_id[:8]} | {run.started_at.strftime('%Y-%m-%d %H:%M')} | {duration}",
            expanded=False,
        ):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Articles", run.articles_processed or 0)
            c2.metric("Injected", run.links_injected or 0)
            c3.metric("Matches", run.matches_found or 0)
            c4.metric("Errors", run.errors_total or 0)
            st.caption(f"Run ID: {run.run_id}")

    session.close()