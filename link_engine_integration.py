"""
link_engine_integration.py
-----------------------------------------------------------------------------
Facade between the FastAPI dashboard and the link_engine package.

READ API (in-process, fast):
  - get_snapshot()         - everything for the dashboard tabs
  - get_pending_for_review()
  - approve_anchor(), reject_anchor(), edit_anchor_text()
  - inject_all_approved()
  - get_injected_with_content()  - injected articles + their full markdown

WRITE / RUN API (also in-process now — subprocess hop removed):
  - ingest_single_article()  - replaces "Add Article" tab
  - ingest_bulk_files()      - replaces "Bulk Upload" file mode
  - ingest_bulk_paste()      - replaces "Bulk Upload" paste mode
  - reprocess_corpus()       - replaces "Reprocess All" tab
  - delete_article_by_slug()
  - restore_article_from_backup()
  - get_all_articles_with_content()
  - get_errors()
  - get_run_history()

Long subprocess passes (cluster_pass / global_pass) are still wrapped in
JobManager from app.py — see existing routes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
import shutil

try:
    from link_engine.db.session import get_session_factory
    from link_engine.db.models import Anchor, Article as LEArticle, Match, Injection
    try:
        from link_engine.db.models import Error as LEError
    except ImportError:
        LEError = None
    try:
        from link_engine.db.models import Run as LinkRun
    except ImportError:
        LinkRun = None
    try:
        from link_engine.stages.inject import inject_approved_links
    except ImportError:
        inject_approved_links = None
    try:
        from link_engine.stages.article_ops import (
            process_single_article,
            process_directory,
            reprocess_all,
            delete_article as le_delete_article,
            split_multi_article_paste,
        )
    except ImportError:
        process_single_article = None
        process_directory = None
        reprocess_all = None
        le_delete_article = None
        split_multi_article_paste = None
    LINK_ENGINE_AVAILABLE = True
    _IMPORT_ERR = None
except Exception as e:                                    # pragma: no cover
    LINK_ENGINE_AVAILABLE = False
    _IMPORT_ERR = repr(e)


# Where uploaded / ingested articles live on disk
CONTENT_DIR = Path("data/link_engine_content")
CONTENT_DIR.mkdir(parents=True, exist_ok=True)


# ─── Read API ────────────────────────────────────────────────────────────

@dataclass
class InterlinkSnapshot:
    pending: list[dict]
    approved: list[dict]
    injected: list[dict]
    articles: list[dict]
    errors: list[dict]
    counts: dict[str, int]
    available: bool
    error: Optional[str] = None


def _safe_session():
    if not LINK_ENGINE_AVAILABLE:
        return None
    return get_session_factory()()


def get_snapshot(limit_pending: int = 100,
                 limit_approved: int = 200,
                 limit_injected: int = 200,
                 limit_articles: int = 500,
                 limit_errors: int = 50) -> InterlinkSnapshot:
    if not LINK_ENGINE_AVAILABLE:
        return InterlinkSnapshot(
            pending=[], approved=[], injected=[], articles=[], errors=[],
            counts={"pending": 0, "approved": 0, "injected": 0, "articles": 0, "errors": 0},
            available=False,
            error=_IMPORT_ERR,
        )

    session = _safe_session()
    pending, approved, injected, articles, errors = [], [], [], [], []

    try:
        for a in (session.query(Anchor)
                  .filter(Anchor.status == "pending_review")
                  .join(Anchor.match)
                  .order_by(Match.similarity_score.desc())
                  .limit(limit_pending).all()):
            try:
                m = a.match
                pending.append({
                    "anchor_id":    a.anchor_id,
                    "anchor_text":  a.edited_anchor or a.anchor_text or m.matched_phrase,
                    "source_title": _safe(lambda: m.source_chunk.article.title),
                    "source_slug":  _safe(lambda: m.source_chunk.article.slug),
                    "target_title": _safe(lambda: m.target_chunk.article.title),
                    "target_slug":  _safe(lambda: m.target_chunk.article.slug),
                    "source_text":  _safe(lambda: (m.source_chunk.text or "")[:600]),
                    "target_text":  _safe(lambda: (m.target_chunk.text or "")[:600]),
                    "similarity":   round(m.similarity_score, 3),
                    "confidence":   a.llm_confidence or 0,
                    "reasoning":    a.reasoning or "",
                    "score_band":   _band(m.similarity_score),
                })
            except Exception as row_err:
                pending.append({"error": str(row_err)})

        injected_ids = {row[0] for row in session.query(Injection.anchor_id).all()}
        for a in (session.query(Anchor)
                  .filter(Anchor.status == "approved")
                  .limit(limit_approved).all()):
            if a.anchor_id in injected_ids:
                continue
            try:
                m = a.match
                approved.append({
                    "anchor_id":    a.anchor_id,
                    "anchor_text":  a.edited_anchor or a.anchor_text or m.matched_phrase,
                    "source_title": _safe(lambda: m.source_chunk.article.title),
                    "target_title": _safe(lambda: m.target_chunk.article.title),
                    "confidence":   a.llm_confidence or 0,
                })
            except Exception:
                pass

        for inj in (session.query(Injection)
                    .order_by(Injection.created_at.desc())
                    .limit(limit_injected).all()):
            anc = session.get(Anchor, inj.anchor_id)
            if not anc:
                continue
            try:
                m = anc.match
                injected.append({
                    "anchor_text":  anc.edited_anchor or anc.anchor_text or m.matched_phrase,
                    "source_title": _safe(lambda: m.source_chunk.article.title),
                    "target_title": _safe(lambda: m.target_chunk.article.title),
                    "injected_at":  str(inj.created_at) if inj.created_at else "",
                })
            except Exception:
                pass

        for art in (session.query(LEArticle)
                    .order_by(LEArticle.created_at.desc())
                    .limit(limit_articles).all()):
            articles.append({
                "article_id":  art.article_id,
                "title":       art.title,
                "slug":        getattr(art, "slug", "") or "",
                "url":         getattr(art, "url", "") or "",
                "chunk_count": len(art.chunks) if hasattr(art, "chunks") else 0,
                "created_at":  str(art.created_at) if art.created_at else "",
            })

        if LEError is not None:
            for e in (session.query(LEError)
                      .order_by(LEError.created_at.desc())
                      .limit(limit_errors).all()):
                errors.append({
                    "stage":      getattr(e, "stage", "") or "",
                    "error_type": getattr(e, "error_type", "") or "",
                    "message":    getattr(e, "message", "") or "",
                    "created_at": str(e.created_at) if e.created_at else "",
                })

    finally:
        session.close()

    counts = {
        "pending":   len(pending),
        "approved":  len(approved),
        "injected":  len(injected),
        "articles":  len(articles),
        "errors":    len(errors),
    }

    return InterlinkSnapshot(
        pending=pending, approved=approved, injected=injected,
        articles=articles, errors=errors, counts=counts,
        available=True,
    )


def approve_anchor(anchor_id: str) -> bool:
    if not LINK_ENGINE_AVAILABLE:
        return False
    session = _safe_session()
    try:
        a = session.get(Anchor, anchor_id)
        if not a:
            return False
        a.status = "approved"
        session.commit()
        return True
    finally:
        session.close()


def reject_anchor(anchor_id: str) -> bool:
    if not LINK_ENGINE_AVAILABLE:
        return False
    session = _safe_session()
    try:
        a = session.get(Anchor, anchor_id)
        if not a:
            return False
        a.status = "rejected"
        session.commit()
        return True
    finally:
        session.close()


def edit_anchor_text(anchor_id: str, new_text: str) -> bool:
    if not LINK_ENGINE_AVAILABLE or not new_text or not new_text.strip():
        return False
    session = _safe_session()
    try:
        a = session.get(Anchor, anchor_id)
        if not a:
            return False
        a.edited_anchor = new_text.strip()
        session.commit()
        return True
    finally:
        session.close()


def inject_all_approved(dry_run: bool = False) -> dict:
    if not LINK_ENGINE_AVAILABLE or inject_approved_links is None or LinkRun is None:
        return {"injected": 0, "errors": 0, "skipped": 0,
                "error": "link_engine not available"}

    session = _safe_session()
    try:
        approved = (
            session.query(Anchor)
            .filter(Anchor.status == "approved")
            .filter(~Anchor.anchor_id.in_(session.query(Injection.anchor_id)))
            .all()
        )
        link_run = LinkRun(articles_processed=0)
        session.add(link_run)
        session.flush()
        results = inject_approved_links(approved, session, link_run.run_id, dry_run=dry_run)
        session.commit()
        return results
    finally:
        session.close()


def get_pending_for_review(limit: int = 100) -> list[dict]:
    snap = get_snapshot(limit_pending=limit, limit_approved=0, limit_injected=0,
                        limit_articles=0, limit_errors=0)
    return snap.pending


# ─── NEW: Write / Run API ─────────────────────────────────────────────────

def ingest_single_article(
    title: str,
    body: str,
    slug: str = "",
    url: str = "",
    article_type: str = "",
    overwrite: bool = True,
) -> dict:
    """Ingest one article from raw fields. Replaces the Streamlit 'Add Article' tab."""
    if not LINK_ENGINE_AVAILABLE or process_single_article is None:
        return {"ok": False, "error": "link_engine not available"}

    import frontmatter as fm
    slug = (slug or "").strip() or _slugify(title)
    url = (url or "").strip() or f"/{slug}"

    file_path = CONTENT_DIR / f"{slug}.md"
    if file_path.exists() and not overwrite:
        return {"ok": False, "error": f"slug '{slug}' already exists"}

    meta = {"title": title, "slug": slug, "url": url}
    if article_type:
        meta["article_type"] = article_type

    file_path.write_text(fm.dumps(fm.Post(body, **meta)), encoding="utf-8")

    session = _safe_session()
    try:
        summary = process_single_article(file_path, session)
        return {"ok": True, "summary": summary, "file": str(file_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


def ingest_bulk_files(uploaded_files: list[dict], overwrite: bool = True,
                      progress_cb: Callable[[str, float], None] = None) -> dict:
    """
    uploaded_files: list of {"filename": str, "raw": str (utf-8 markdown)}
    Returns the same shape as process_directory.
    """
    if not LINK_ENGINE_AVAILABLE or process_directory is None:
        return {"ok": False, "error": "link_engine not available"}

    import frontmatter as fm

    session = _safe_session()
    try:
        existing_slugs = {a[0] for a in session.query(LEArticle.slug).all()}
    finally:
        session.close()

    written, skipped = [], []
    for f in uploaded_files:
        raw = f["raw"]
        try:
            parsed = fm.loads(raw)
            t = parsed.metadata.get("title", f["filename"].replace(".md", ""))
            s = parsed.metadata.get("slug") or _slugify(t)
            body = parsed.content
            meta = dict(parsed.metadata)
        except Exception:
            t = f["filename"].replace(".md", "")
            s = _slugify(t)
            body = raw
            meta = {}

        if s in existing_slugs and not overwrite:
            skipped.append(f["filename"])
            continue

        meta["title"] = t
        meta["slug"] = s
        if "url" not in meta:
            meta["url"] = f"/{s}"

        (CONTENT_DIR / f"{s}.md").write_text(fm.dumps(fm.Post(body, **meta)), encoding="utf-8")
        written.append(f["filename"])

    if not written:
        return {"ok": False, "error": "nothing written", "skipped": skipped}

    session = _safe_session()
    try:
        summary = process_directory(CONTENT_DIR, session, progress_callback=progress_cb)
        return {"ok": True, "summary": summary, "written": written, "skipped": skipped}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


def ingest_bulk_paste(pasted: str, overwrite: bool = True,
                      progress_cb: Callable[[str, float], None] = None) -> dict:
    """Parse one big concatenated markdown blob into separate articles, ingest each."""
    if not LINK_ENGINE_AVAILABLE or split_multi_article_paste is None:
        return {"ok": False, "error": "link_engine not available"}

    articles = split_multi_article_paste(pasted)
    if not articles:
        return {"ok": False, "error": "no articles detected"}

    files = []
    for a in articles:
        files.append({
            "filename": f"{a['slug'] or _slugify(a['title'])}.md",
            "raw": _rebuild_md(a),
        })
    return ingest_bulk_files(files, overwrite=overwrite, progress_cb=progress_cb)


def reprocess_corpus(invalidate_phrases: bool = False,
                     invalidate_embeddings: bool = False,
                     invalidate_confidence: bool = False,
                     progress_cb: Callable[[str, float], None] = None) -> dict:
    if not LINK_ENGINE_AVAILABLE or reprocess_all is None:
        return {"ok": False, "error": "link_engine not available"}
    session = _safe_session()
    try:
        summary = reprocess_all(
            CONTENT_DIR, session,
            invalidate_phrases=invalidate_phrases,
            invalidate_embeddings=invalidate_embeddings,
            invalidate_confidence=invalidate_confidence,
            progress_callback=progress_cb,
        )
        return {"ok": True, "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


def delete_article_by_id(article_id: str, delete_file: bool = False) -> bool:
    if not LINK_ENGINE_AVAILABLE or le_delete_article is None:
        return False
    session = _safe_session()
    try:
        article = session.get(LEArticle, article_id)
        if not article:
            return False
        file_path = Path(article.file_path) if article.file_path else None
        ok = le_delete_article(article_id, session)
        if ok and delete_file and file_path and file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass
        return ok
    finally:
        session.close()


def get_all_articles_with_content(limit: int = 500) -> list[dict]:
    """For the 'All Articles' tab — list with chunk count and content preview."""
    if not LINK_ENGINE_AVAILABLE:
        return []
    import frontmatter as fm
    session = _safe_session()
    out = []
    try:
        for art in (session.query(LEArticle)
                    .order_by(LEArticle.title)
                    .limit(limit).all()):
            chunks_sorted = sorted(
                art.chunks, key=lambda c: c.position_index or 0
            ) if hasattr(art, "chunks") else []

            content = ""
            file_path = Path(art.file_path) if art.file_path else None
            if file_path and file_path.exists():
                try:
                    raw = file_path.read_text(encoding="utf-8")
                    post = fm.loads(raw)
                    content = post.content
                except Exception:
                    pass

            injected_count = 0
            try:
                for chunk in chunks_sorted:
                    for match in chunk.source_matches:
                        if match.anchor and match.anchor.injection and \
                           match.anchor.injection.status == "injected":
                            injected_count += 1
            except Exception:
                pass

            out.append({
                "article_id":     art.article_id,
                "title":          art.title,
                "slug":           getattr(art, "slug", "") or "",
                "url":            getattr(art, "url", "") or "",
                "status":         getattr(art, "status", "") or "",
                "file_path":      str(file_path) if file_path else "",
                "chunk_count":    len(chunks_sorted),
                "injected_count": injected_count,
                "content":        content,
                "sections": [
                    {"heading": c.heading or "Introduction", "word_count": c.word_count or 0}
                    for c in chunks_sorted
                ],
            })
    finally:
        session.close()
    return out


def get_injected_with_content(limit: int = 200) -> list[dict]:
    """
    For the 'Injected Posts' tab — group injections by article, return article body
    plus the list of links injected, plus backup file path.
    """
    if not LINK_ENGINE_AVAILABLE:
        return []

    import frontmatter as fm
    session = _safe_session()
    by_article: dict[str, dict] = {}

    try:
        injections = (
            session.query(Injection)
            .filter_by(status="injected")
            .order_by(Injection.injected_at.desc())
            .limit(limit).all()
        )

        for inj in injections:
            anchor = inj.anchor
            if not anchor or not anchor.match:
                continue
            article = anchor.match.source_chunk.article
            aid = article.article_id

            if aid not in by_article:
                file_path = Path(article.file_path) if article.file_path else None
                raw, content = "", ""
                if file_path and file_path.exists():
                    try:
                        raw = file_path.read_text(encoding="utf-8")
                        post = fm.loads(raw)
                        content = post.content
                    except Exception:
                        pass
                by_article[aid] = {
                    "article_id": aid,
                    "title":      article.title,
                    "slug":       getattr(article, "slug", "") or "",
                    "url":        getattr(article, "url", "") or "",
                    "file_path":  str(file_path) if file_path else "",
                    "raw":        raw,
                    "content":    content,
                    "links":      [],
                    "backup":     "",
                }

            target_article = anchor.match.target_chunk.article
            phrase = anchor.edited_anchor or anchor.anchor_text or ""
            by_article[aid]["links"].append({
                "anchor_text":  phrase,
                "target_title": target_article.title,
                "target_slug":  getattr(target_article, "slug", "") or "",
                "target_url":   getattr(target_article, "url", "") or "",
                "injected_at":  inj.injected_at.strftime("%Y-%m-%d %H:%M") if inj.injected_at else "",
            })
            if not by_article[aid]["backup"] and inj.backup_path:
                by_article[aid]["backup"] = inj.backup_path

    finally:
        session.close()

    return list(by_article.values())


def restore_article_from_backup(article_id: str) -> bool:
    """Restore an article from its backup_path and mark the injections as skipped."""
    if not LINK_ENGINE_AVAILABLE:
        return False
    session = _safe_session()
    try:
        article = session.get(LEArticle, article_id)
        if not article:
            return False
        file_path = Path(article.file_path) if article.file_path else None

        injections = []
        for chunk in (article.chunks or []):
            for match in chunk.source_matches:
                if match.anchor and match.anchor.injection:
                    injections.append(match.anchor.injection)

        backup_path = None
        for inj in injections:
            if inj.backup_path and Path(inj.backup_path).exists():
                backup_path = Path(inj.backup_path)
                break

        if not backup_path or not file_path:
            return False

        shutil.copy2(backup_path, file_path)
        for inj in injections:
            inj.status = "skipped"
            inj.error_message = "Manually restored"
        session.commit()
        return True
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()


def get_run_history(limit: int = 20) -> list[dict]:
    if not LINK_ENGINE_AVAILABLE or LinkRun is None:
        return []
    session = _safe_session()
    out = []
    try:
        for r in (session.query(LinkRun)
                  .order_by(LinkRun.started_at.desc())
                  .limit(limit).all()):
            duration = ""
            if r.completed_at and r.started_at:
                secs = (r.completed_at - r.started_at).total_seconds()
                duration = f"{secs:.1f}s"
            out.append({
                "run_id":              r.run_id,
                "started_at":          r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "",
                "duration":            duration,
                "articles_processed":  r.articles_processed or 0,
                "matches_found":       r.matches_found or 0,
                "links_injected":      r.links_injected or 0,
                "errors_total":        r.errors_total or 0,
            })
    finally:
        session.close()
    return out


# ─── Run API (subprocess via the existing bridge) ─────────────────────────

def run_cluster_pass(cluster_id: str, run_id: str) -> dict:
    from link_engine_bridge import cluster_pass
    return cluster_pass(cluster_id, run_id)


def run_global_pass(cluster_id: str, run_id: str) -> dict:
    from link_engine_bridge import global_pass
    return global_pass(cluster_id, run_id)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _safe(fn, default: str = "") -> str:
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def _band(score: float) -> str:
    if score >= 0.80: return "high"
    if score >= 0.65: return "mid"
    return "low"


def _slugify(text: str) -> str:
    import re
    s = (text or "").lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")[:80] or "untitled"


def _rebuild_md(article: dict) -> str:
    """Take a dict from split_multi_article_paste and serialize back to .md text."""
    import frontmatter as fm
    meta = dict(article.get("metadata") or {})
    meta["title"] = article["title"]
    meta["slug"] = article["slug"] or _slugify(article["title"])
    if "url" not in meta:
        meta["url"] = f"/{meta['slug']}"
    return fm.dumps(fm.Post(article["body"], **meta))