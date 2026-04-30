"""
Pipeline helpers callable from the dashboard.
"""
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import frontmatter as fm

from link_engine.db.models import (
    Anchor, Article, Chunk, Embedding, Injection, Match, Run,
)
from link_engine.stages.anchor import generate_all_anchors
from link_engine.stages.chunk import chunk_all_articles, chunk_article
from link_engine.stages.embed import embed_all_pending, embed_article_representations
from link_engine.stages.ingest import ingest_directory, ingest_file
from link_engine.stages.match import compute_matches


def process_single_article(file_path: Path, session) -> dict:
    run = Run()
    session.add(run)
    session.flush()
    run_id = run.run_id

    summary = {
        "run_id": run_id, "article": None, "status": None,
        "chunks": 0, "new_embeddings": 0, "new_representations": 0,
        "matches_found": 0, "anchors_passed": 0, "anchors_errored": 0,
        "errors": [],
    }

    article = ingest_file(file_path, run_id, session)
    if article is None:
        summary["errors"].append("Ingestion failed")
        session.commit()
        return summary

    session.flush()
    summary["article"] = article.title
    summary["status"] = article.status

    if article.status == "unchanged":
        summary["matches_found"] = compute_matches(session, run_id)
        if summary["matches_found"] > 0:
            ar = generate_all_anchors(session, run_id)
            summary["anchors_passed"] = ar["success"]
            summary["anchors_errored"] = ar["errors"]
        run.completed_at = datetime.utcnow()
        session.commit()
        return summary

    chunks = chunk_article(article, session)
    summary["chunks"] = len(chunks)
    session.flush()

    summary["new_embeddings"] = embed_all_pending(session)
    session.flush()
    summary["new_representations"] = embed_article_representations(session)
    session.flush()
    summary["matches_found"] = compute_matches(session, run_id)
    session.flush()

    if summary["matches_found"] > 0:
        ar = generate_all_anchors(session, run_id)
        summary["anchors_passed"] = ar["success"]
        summary["anchors_errored"] = ar["errors"]

    run.articles_processed = 1
    run.chunks_created = summary["chunks"]
    run.embeddings_computed = summary["new_embeddings"]
    run.matches_found = summary["matches_found"]
    run.completed_at = datetime.utcnow()
    session.commit()
    return summary


def process_directory(
    directory: Path,
    session,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> dict:
    def _p(msg: str, frac: float):
        if progress_callback:
            progress_callback(msg, frac)

    run = Run()
    session.add(run)
    session.flush()
    run_id = run.run_id

    summary = {
        "run_id": run_id, "new": 0, "changed": 0, "unchanged": 0,
        "ingestion_errors": 0, "chunks": 0, "new_embeddings": 0,
        "new_representations": 0, "matches_found": 0,
        "anchors_passed": 0, "anchors_errored": 0,
    }

    _p("Ingesting articles and extracting phrases...", 0.05)
    results = ingest_directory(directory, run_id, session)
    session.flush()
    changed = results["new"] + results["changed"]
    summary["new"] = len(results["new"])
    summary["changed"] = len(results["changed"])
    summary["unchanged"] = len(results["unchanged"])
    summary["ingestion_errors"] = results["errors"]

    _p(f"Chunking {len(changed)} article(s)...", 0.30)
    if changed:
        summary["chunks"] = chunk_all_articles(changed, session)
        session.flush()

    _p("Computing chunk embeddings...", 0.50)
    summary["new_embeddings"] = embed_all_pending(session)
    session.flush()

    _p("Computing article representations...", 0.65)
    summary["new_representations"] = embed_article_representations(session)
    session.flush()

    _p("Finding semantic matches...", 0.75)
    summary["matches_found"] = compute_matches(session, run_id)
    session.flush()

    if summary["matches_found"] > 0:
        _p(f"Scoring {summary['matches_found']} candidate link(s) with LLM...", 0.85)
        ar = generate_all_anchors(session, run_id)
        summary["anchors_passed"] = ar["success"]
        summary["anchors_errored"] = ar["errors"]

    run.articles_processed = len(changed)
    run.chunks_created = summary["chunks"]
    run.embeddings_computed = summary["new_embeddings"]
    run.matches_found = summary["matches_found"]
    run.completed_at = datetime.utcnow()
    session.commit()
    _p("Complete.", 1.0)
    return summary


def reprocess_all(
    directory: Path,
    session,
    invalidate_phrases: bool = False,
    invalidate_embeddings: bool = False,
    invalidate_confidence: bool = False,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> dict:
    """
    Selectively invalidate caches, then re-run the pipeline.

    invalidate_phrases: clear title_phrases_json → forces LLM re-extraction ($)
    invalidate_embeddings: delete embeddings + reps → recomputed locally (free)
    invalidate_confidence: delete matches + anchors → forces LLM re-scoring ($)
    """
    def _p(msg: str, frac: float):
        if progress_callback:
            progress_callback(msg, frac)

    if invalidate_confidence:
        _p("Clearing matches and anchors...", 0.02)
        session.query(Injection).delete(synchronize_session=False)
        session.query(Anchor).delete(synchronize_session=False)
        session.query(Match).delete(synchronize_session=False)
        session.flush()

    if invalidate_embeddings:
        _p("Clearing embeddings...", 0.04)
        session.query(Embedding).delete(synchronize_session=False)
        for a in session.query(Article).all():
            a.representation_vector = None
            a.representation_hash = None
        session.flush()

    if invalidate_phrases:
        _p("Clearing extracted phrases...", 0.06)
        for a in session.query(Article).all():
            a.title_phrases_json = None
        session.flush()

    summary = process_directory(directory, session, progress_callback=progress_callback)
    summary["invalidated"] = {
        "phrases": invalidate_phrases,
        "embeddings": invalidate_embeddings,
        "confidence": invalidate_confidence,
    }
    return summary


def delete_article(article_id: str, session) -> bool:
    article = session.get(Article, article_id)
    if not article:
        return False

    chunk_ids = [c.chunk_id for c in article.chunks]
    if chunk_ids:
        matches = session.query(Match).filter(
            (Match.source_chunk_id.in_(chunk_ids)) |
            (Match.target_chunk_id.in_(chunk_ids))
        ).all()
        match_ids = [m.match_id for m in matches]

        if match_ids:
            anchors = session.query(Anchor).filter(Anchor.match_id.in_(match_ids)).all()
            anchor_ids = [a.anchor_id for a in anchors]
            if anchor_ids:
                session.query(Injection).filter(Injection.anchor_id.in_(anchor_ids)).delete(
                    synchronize_session=False
                )
                session.query(Anchor).filter(Anchor.anchor_id.in_(anchor_ids)).delete(
                    synchronize_session=False
                )
            session.query(Match).filter(Match.match_id.in_(match_ids)).delete(
                synchronize_session=False
            )
        session.query(Embedding).filter(Embedding.chunk_id.in_(chunk_ids)).delete(
            synchronize_session=False
        )
        session.query(Chunk).filter(Chunk.chunk_id.in_(chunk_ids)).delete(
            synchronize_session=False
        )

    session.delete(article)
    session.commit()
    return True


def split_multi_article_paste(text: str) -> list:
    """
    Split a single textarea containing one or more frontmatter-delimited articles.

    Each article block is expected to look like a normal .md file:
        ---
        title: ...
        slug: ...
        ---
        ## Body...

    Multiple articles can be concatenated with a blank line between them.
    Returns a list of {raw, title, slug, body, metadata} dicts.
    """
    lines = text.split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        if i >= len(lines):
            break
        start = i  # opening ---
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        if j >= len(lines):
            break
        # j is the closing --- of frontmatter
        # body runs from j+1 until the next opening ---
        k = j + 1
        while k < len(lines) and lines[k].strip() != "---":
            k += 1
        block_text = "\n".join(lines[start:k])
        blocks.append(block_text)
        i = k

    articles = []
    for block in blocks:
        try:
            post = fm.loads(block)
            meta = dict(post.metadata)
            title = (meta.get("title") or "").strip()
            slug = (meta.get("slug") or "").strip()
            body = post.content
            if not title:
                continue
            articles.append({
                "raw": block,
                "title": title,
                "slug": slug,
                "body": body,
                "metadata": meta,
            })
        except Exception:
            continue

    return articles