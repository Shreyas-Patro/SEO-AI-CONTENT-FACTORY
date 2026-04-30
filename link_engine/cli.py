#!/usr/bin/env python3
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="AI-powered internal link engine (title-anchored edition)")
console = Console()


def _get_session_and_run():
    from link_engine.config import get_config
    from link_engine.db.models import Run
    from link_engine.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    cfg = get_config()
    run = Run(config_json=json.dumps({k: v for k, v in cfg.items() if k != "anthropic_api_key"}))
    session.add(run)
    session.flush()
    return session, run


@app.command()
def run(
    content_dir: Path = typer.Argument(..., help="Directory of markdown files"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview injections without writing"),
):
    from link_engine.db.models import Run
    from link_engine.stages.anchor import generate_all_anchors
    from link_engine.stages.chunk import chunk_all_articles
    from link_engine.stages.embed import embed_all_pending, embed_article_representations
    from link_engine.stages.ingest import ingest_directory
    from link_engine.stages.match import compute_matches

    if not content_dir.exists():
        console.print(f"[red]Directory not found: {content_dir}[/red]")
        raise typer.Exit(1)

    session, pipeline_run = _get_session_and_run()
    run_id = pipeline_run.run_id
    console.print(f"\n[bold blue]🔗 Link Engine[/bold blue]  Run ID: {run_id[:8]}...\n")

    # Stage 1: Ingest (now also derives title_phrases)
    console.print("[bold]Stage 1/6:[/bold] Ingesting articles...")
    results = ingest_directory(content_dir, run_id, session)
    session.commit()
    changed = results["new"] + results["changed"]
    console.print(
        f"  ✓ {len(results['new'])} new  |  {len(results['changed'])} changed  |  "
        f"{len(results['unchanged'])} unchanged  |  {results['errors']} errors"
    )
    pipeline_run.articles_processed = len(changed)

    if not changed and len(results["unchanged"]) == 0:
        console.print("[yellow]No articles to process.[/yellow]")
        session.commit()
        session.close()
        return

    # Stage 2: Chunk
    console.print("[bold]Stage 2/6:[/bold] Chunking articles...")
    total_chunks = chunk_all_articles(changed, session) if changed else 0
    session.commit()
    console.print(f"  ✓ {total_chunks} chunks created")
    pipeline_run.chunks_created = total_chunks

    # Stage 3: Embed chunks
    console.print("[bold]Stage 3/6:[/bold] Computing chunk embeddings (cached)...")
    n_computed = embed_all_pending(session)
    session.commit()
    console.print(f"  ✓ {n_computed} new chunk embeddings computed")
    pipeline_run.embeddings_computed = n_computed

    # Stage 4: Embed article representations (NEW)
    console.print("[bold]Stage 4/6:[/bold] Computing article representation vectors...")
    n_reps = embed_article_representations(session)
    session.commit()
    console.print(f"  ✓ {n_reps} article representations computed")

    # Stage 5: Match (NEW ALGORITHM — title-phrase lexical + semantic gate)
    console.print("[bold]Stage 5/6:[/bold] Finding title-phrase matches (semantic-gated)...")
    n_matches = compute_matches(session, run_id)
    session.commit()
    console.print(f"  ✓ {n_matches} new matches found")
    pipeline_run.matches_found = n_matches

    # Stage 6: LLM confidence scoring (no longer "anchor generation")
    console.print("[bold]Stage 6/6:[/bold] LLM confidence scoring...")
    anchor_results = generate_all_anchors(session, run_id)
    session.commit()
    console.print(
        f"  ✓ {anchor_results['success']} links passed confidence gate  |  "
        f"{anchor_results['errors']} rejected/errored"
    )

    pipeline_run.completed_at = datetime.utcnow()
    session.commit()

    console.print(
        f"\n[bold green]✓ Pipeline complete![/bold green]\n"
        f"  Launch dashboard to review:\n"
        f"  [cyan]python -m link_engine.cli dashboard[/cyan]\n"
    )
    session.close()


@app.command()
def dashboard():
    console.print("[bold blue]Launching dashboard...[/bold blue]")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run",
        "link_engine/dashboard/app.py",
        "--server.headless", "true",
    ])


@app.command()
def status():
    from link_engine.db.models import Anchor, Article, Error, Match
    from link_engine.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()

    table = Table(title="Pipeline Status")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("Total articles", str(session.query(Article).count()))
    table.add_row("Pending review", str(session.query(Anchor).filter_by(status="pending_review").count()))
    table.add_row("Approved links", str(session.query(Anchor).filter_by(status="approved").count()))
    table.add_row("Rejected links", str(session.query(Anchor).filter_by(status="rejected").count()))
    table.add_row("Match errors", str(session.query(Match).filter_by(status="anchor_error").count()))
    table.add_row("Unresolved errors", str(session.query(Error).filter(Error.resolved_at.is_(None)).count()))

    console.print(table)
    session.close()


@app.command()
def rerun(
    error_id: Optional[str] = typer.Option(None, "--error-id"),
    stage: Optional[str] = typer.Option(None, "--stage"),
    all_errors: bool = typer.Option(False, "--all-errors"),
):
    from link_engine.db.models import Chunk, Error, Match
    from link_engine.db.session import get_session_factory
    from link_engine.stages.anchor import evaluate_match
    from link_engine.stages.embed import embed_chunks

    factory = get_session_factory()
    session = factory()

    query = session.query(Error).filter(Error.resolved_at.is_(None), Error.rerun_eligible == True)

    if error_id:
        query = query.filter(Error.error_id == error_id)
    elif stage:
        query = query.filter(Error.stage == stage)
    elif not all_errors:
        console.print("[red]Specify --error-id, --stage, or --all-errors[/red]")
        raise typer.Exit(1)

    errors = query.all()
    if not errors:
        console.print("[yellow]No eligible errors found.[/yellow]")
        return

    console.print(f"Rerunning {len(errors)} error(s)...")
    for err in errors:
        try:
            if err.stage == "anchor" and err.match_id:
                match = session.get(Match, err.match_id)
                if match:
                    match.status = "pending_anchor"
                    session.flush()
                    if evaluate_match(match, session):
                        err.resolved_at = datetime.utcnow()
                        console.print(f"  ✓ Rescored match: {err.error_id[:8]}")
            elif err.stage == "embedding" and err.chunk_id:
                chunk = session.get(Chunk, err.chunk_id)
                if chunk:
                    embed_chunks([chunk], session)
                    err.resolved_at = datetime.utcnow()
                    console.print(f"  ✓ Embedding rerun: {err.error_id[:8]}")
            else:
                console.print(f"  → Manual rerun for stage '{err.stage}': {err.error_id[:8]}")
            session.commit()
        except Exception as e:
            session.rollback()
            console.print(f"  ✗ Rerun failed: {e}")

    session.close()


if __name__ == "__main__":
    app()