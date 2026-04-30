import json
from datetime import datetime
from pathlib import Path

from link_engine.db.models import Anchor, Injection, Error, Run
from link_engine.config import get_config


def write_reports(run_id: str, session):
    cfg = get_config()
    output_dir = Path(cfg.get("output_dir", "./output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Link Report ---
    injections = (
        session.query(Injection)
        .filter_by(run_id=run_id, status="injected")
        .all()
    )

    link_report = []
    for inj in injections:
        anchor = inj.anchor
        match = anchor.match
        link_report.append({
            "injection_id": inj.injection_id,
            "source_article_title": match.source_chunk.article.title,
            "source_article_slug": match.source_chunk.article.slug,
            "source_chunk_heading": match.source_chunk.heading,
            "target_article_title": match.target_chunk.article.title,
            "target_article_slug": match.target_chunk.article.slug,
            "target_chunk_heading": match.target_chunk.heading,
            "anchor_text": anchor.edited_anchor or anchor.anchor_text,
            "similarity_score": round(match.similarity_score, 4),
            "llm_confidence": anchor.llm_confidence,
            "injected_at": inj.injected_at.isoformat() if inj.injected_at else None,
        })

    (output_dir / "link_report.json").write_text(
        json.dumps(link_report, indent=2), encoding="utf-8"
    )

    # --- Error Report ---
    errors = session.query(Error).filter_by(run_id=run_id).all()
    error_report = [
        {
            "error_id": e.error_id,
            "stage": e.stage,
            "article_id": e.article_id,
            "chunk_id": e.chunk_id,
            "error_type": e.error_type,
            "message": e.message,
            "rerun_eligible": e.rerun_eligible,
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        }
        for e in errors
    ]

    (output_dir / "error_report.json").write_text(
        json.dumps(error_report, indent=2), encoding="utf-8"
    )

    # --- Human Summary ---
    run = session.get(Run, run_id)
    duration = ""
    if run and run.completed_at and run.started_at:
        secs = (run.completed_at - run.started_at).total_seconds()
        duration = f"{secs:.1f}s"

    summary = f"""# Link Engine Run Summary
Run ID: {run_id}
Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
Duration: {duration}

## Results
- Articles processed: {run.articles_processed if run else '?'}
- Chunks created: {run.chunks_created if run else '?'}
- Embeddings computed: {run.embeddings_computed if run else '?'}
- Matches found: {run.matches_found if run else '?'}
- Links injected: {len(link_report)}
- Errors: {len(error_report)}

## Links Injected
"""
    for link in link_report:
        summary += f"- [{link['anchor_text']}] {link['source_article_title']} → {link['target_article_title']} (score: {link['similarity_score']})\n"

    if error_report:
        summary += "\n## Errors\n"
        for err in error_report:
            summary += f"- [{err['stage']}] {err['error_type']}: {err['message'][:80]}\n"

    (output_dir / "run_summary.md").write_text(summary, encoding="utf-8")
    print(f"✓ Reports written to {output_dir}/")