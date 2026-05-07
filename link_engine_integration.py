"""
link_engine_integration.py
-----------------------------------------------------------------------------
A clean, single-import facade between the FastAPI dashboard and the
existing link_engine package.

DESIGN
    - READS (list pending anchors, list approved, list injected, list articles,
      list errors, approve/reject by id, get counts) — direct Python imports
      from link_engine.db. Fast, in-process, no subprocess overhead.
    - RUNS (cluster_pass, global_pass) — keep the subprocess approach from
      link_engine_bridge.py. Long-running, isolated, can crash without taking
      the web server down. Launched via JobManager.

WHY HYBRID
    The Streamlit app talked to link_engine in-process and that worked fine.
    The bridge added subprocess isolation specifically because link_engine
    runs an LLM scoring loop that can take minutes — you don't want that
    blocking your event loop. But a plain SQL read for "what anchors are
    pending review?" is microseconds and doesn't need isolation.

CALL THIS FROM app.py — never reach into link_engine internals from a route.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# These imports are deferred-tolerant. If the link_engine package isn't
# importable (e.g. bad sys.path on a fresh checkout), the routes fall back
# to empty results rather than crashing the whole app.
try:
    from link_engine.db.session import get_session_factory
    from link_engine.db.models import Anchor, Article as LEArticle, Match, Injection
    try:
        from link_engine.db.models import Error as LEError
    except ImportError:
        LEError = None
    try:
        from link_engine.stages.inject import inject_approved_links
    except ImportError:
        inject_approved_links = None
    try:
        from link_engine.db.models import Run as LinkRun
    except ImportError:
        LinkRun = None
    LINK_ENGINE_AVAILABLE = True
    _IMPORT_ERR = None
except Exception as e:                                    # pragma: no cover
    LINK_ENGINE_AVAILABLE = False
    _IMPORT_ERR = repr(e)


# ─── Read API ────────────────────────────────────────────────────────────

@dataclass
class InterlinkSnapshot:
    """Everything the interlink dashboard needs in one round-trip."""
    pending: list[dict]
    approved: list[dict]
    injected: list[dict]
    articles: list[dict]
    errors: list[dict]
    counts: dict[str, int]
    available: bool
    error: Optional[str] = None


def _safe_session():
    """Return a session or None if the engine isn't importable."""
    if not LINK_ENGINE_AVAILABLE:
        return None
    return get_session_factory()()


def get_snapshot(limit_pending: int = 100,
                 limit_approved: int = 200,
                 limit_injected: int = 200,
                 limit_articles: int = 500,
                 limit_errors: int = 50) -> InterlinkSnapshot:
    """
    Build a one-shot read snapshot for the dashboard. Tolerant of missing
    columns / older schemas — falls back to empty rows on any per-row error.
    """
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
        # PENDING — anchors awaiting human review, ordered by similarity
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

        # APPROVED (excluding already-injected)
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

        # INJECTED
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

        # ARTICLES indexed
        for art in (session.query(LEArticle)
                    .order_by(LEArticle.created_at.desc())
                    .limit(limit_articles).all()):
            articles.append({
                "title":       art.title,
                "slug":        getattr(art, "slug", "") or "",
                "url":         getattr(art, "url", "") or "",
                "chunk_count": len(art.chunks) if hasattr(art, "chunks") else 0,
                "created_at":  str(art.created_at) if art.created_at else "",
            })

        # ERRORS
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
    """Mark an anchor approved. Returns True on success."""
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
    """Mark an anchor rejected. Returns True on success."""
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
    """Update the visible anchor text without changing approval status."""
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
    """
    Run the injection stage for every approved-but-not-yet-injected anchor.
    Returns the result dict from inject_approved_links: {injected, errors, skipped}.
    """
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
    """Just the pending anchors — used for the HTMX partial refresh."""
    snap = get_snapshot(limit_pending=limit, limit_approved=0, limit_injected=0,
                        limit_articles=0, limit_errors=0)
    return snap.pending


# ─── Run API (subprocess via the existing bridge) ─────────────────────────

def run_cluster_pass(cluster_id: str, run_id: str) -> dict:
    """Long-running. Wrap with JobManager — see app.py route."""
    from link_engine_bridge import cluster_pass
    return cluster_pass(cluster_id, run_id)


def run_global_pass(cluster_id: str, run_id: str) -> dict:
    """Long-running. Wrap with JobManager — see app.py route."""
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
    """Bucket similarity into a colour band for the UI."""
    if score >= 0.80: return "high"
    if score >= 0.65: return "mid"
    return "low"