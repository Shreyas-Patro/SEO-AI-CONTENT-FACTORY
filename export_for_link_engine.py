"""
Export articles from the Canvas Homes DB as link-engine-ready .md files.

Two ways to use this:

1. As a CLI script:
       python export_for_link_engine.py                      # all clusters → ./link_engine_export/
       python export_for_link_engine.py --cluster cl-xxxxx   # one cluster
       python export_for_link_engine.py --run prun-xxxxx     # one pipeline run
       python export_for_link_engine.py --zip                # also produce .zip

   Output goes to ./link_engine_export/<cluster_id>/*.md
   You can then bulk-upload that folder (or its zip) to the Streamlit
   interlinking dashboard.

2. Imported by app.py for download buttons (see ROUTES_TO_ADD at the bottom
   of this file).

The exported files match exactly what link_engine/stages/ingest.py expects:

    ---
    title: "..."
    slug: "..."
    url: "/..."
    article_type: "..."
    canonical_url: "..."
    meta_description: "..."
    ---

    # Article Title
    body...

It also strips empty heading-only sections (## Foo\n\n### Bar) which cause
UNIQUE constraint violations in the link engine chunker.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable
from citation_stripper import strip_citations
# Make sure the project root is on sys.path so this script runs from anywhere
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from db.sqlite_ops import db_conn, get_articles_by_cluster
from db.artifacts import list_pipeline_runs, get_pipeline_run


# ── Markdown sanitiser ───────────────────────────────────────────────────

def _sanitize_for_link_engine(body: str) -> str:
     """
    Remove markdown patterns that produce empty chunks in the link engine.
    Also strips citation markers since interlinking shouldn't index "[Source]"
    as anchor text.
    """
    # NEW: strip citations first — they have nothing to interlink against
    body = strip_citations(body)
  """
    Remove markdown patterns that produce empty chunks in the link engine:

    1. Heading-only sections — '## Foo\\n\\n### Bar' becomes '### Bar'
       (the link engine's chunker creates a zero-text chunk for the empty
       section, and a second one collides on UNIQUE(article_id, chunk_hash))

    2. JSON-LD <script> blocks — meta_tagger appends these and they have
       nothing to interlink against, so they only create noise chunks.

    3. Trailing whitespace and excessive blank lines.
    """
    # Strip <script type="application/ld+json"> ... </script> blocks
    body = re.sub(
        r'<script\s+type="application/ld\+json"[^>]*>.*?</script>',
        '',
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Remove generic <script>...</script> too (just in case)
    body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL | re.IGNORECASE)

    # Strip HTML comments
    body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)

    # Collapse heading-only sections: any line starting with ## or ### that
    # is followed only by whitespace/blanks before the next heading.
    lines = body.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Is this a markdown heading?
        is_heading = bool(re.match(r'^#{1,6}\s+\S', stripped))

        if is_heading:
            # Look ahead — is there any non-blank, non-heading text before
            # the next heading or EOF?
            has_content = False
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if re.match(r'^#{1,6}\s+\S', nxt):
                    break  # next heading; current heading has no content
                has_content = True
                break

            if has_content:
                out.append(line)
            # else: skip the empty heading entirely
        else:
            out.append(line)
        i += 1

    cleaned = "\n".join(out)

    # Collapse 3+ consecutive blank lines to 2
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    return cleaned.strip() + "\n"


# ── Frontmatter builder ──────────────────────────────────────────────────

def _yaml_escape(s: str) -> str:
    """Quote a string safely for YAML."""
    if s is None:
        return ""
    s = str(s).replace('"', "'").replace("\n", " ").strip()
    return s


def article_to_link_engine_md(article: dict) -> tuple[str, str]:
    """
    Build a (filename, content) pair for the link engine.

    Frontmatter contains exactly the fields ingest.py reads (slug, title, url)
    plus a few extras the link engine doesn't care about but are useful if
    you re-import elsewhere.
    """
    title = article.get("title") or "Untitled"
    slug = article.get("slug") or _slugify(title)

    # The link engine uses `url` as the canonical path — give it a clean one.
    url = article.get("url") or f"/{slug}"

    article_type = article.get("article_type") or ""
    meta_description = article.get("meta_description") or ""

    # Pull canonical_url out of schema_json
    try:
        schema = json.loads(article.get("schema_json") or "{}")
    except Exception:
        schema = {}
    canonical_url = schema.get("canonical_url") or ""

    fm_lines = ["---"]
    fm_lines.append(f'title: "{_yaml_escape(title)}"')
    fm_lines.append(f'slug: {slug}')
    fm_lines.append(f'url: {url}')
    if article_type:
        fm_lines.append(f'article_type: {article_type}')
    if canonical_url:
        fm_lines.append(f'canonical_url: {canonical_url}')
    if meta_description:
        fm_lines.append(f'meta_description: "{_yaml_escape(meta_description)[:300]}"')
    fm_lines.append("---")
    fm_lines.append("")

    # Sanitize the body for the link engine
    body = _sanitize_for_link_engine(article.get("content_md") or "")

    content = "\n".join(fm_lines) + body
    return f"{slug}.md", content


def _slugify(text: str) -> str:
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80] or "untitled"


# ── DB helpers ───────────────────────────────────────────────────────────

def list_clusters() -> list[dict]:
    """Return distinct cluster_ids from the articles table."""
    with db_conn() as conn:
        rows = conn.execute("""
            SELECT cluster_id, COUNT(*) as n_articles, MIN(created_at) as first_at
            FROM articles
            WHERE cluster_id IS NOT NULL AND cluster_id != ''
            GROUP BY cluster_id
            ORDER BY first_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── Export functions ─────────────────────────────────────────────────────

def export_cluster_to_dir(cluster_id: str, output_dir: Path) -> list[Path]:
    """Write one cluster's articles as .md files into output_dir. Returns paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    articles = get_articles_by_cluster(cluster_id) or []
    written = []
    for a in articles:
        filename, content = article_to_link_engine_md(a)
        path = output_dir / filename
        # Avoid filename collisions (rare, but possible if two articles slugify the same)
        if path.exists():
            stem = path.stem
            n = 2
            while (output_dir / f"{stem}-{n}.md").exists():
                n += 1
            path = output_dir / f"{stem}-{n}.md"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def export_cluster_to_zip(cluster_id: str) -> bytes:
    """Return a zip of one cluster's articles as bytes (in-memory)."""
    articles = get_articles_by_cluster(cluster_id) or []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = {}
        for a in articles:
            filename, content = article_to_link_engine_md(a)
            if filename in seen:
                seen[filename] += 1
                base, ext = filename.rsplit(".", 1)
                filename = f"{base}-{seen[filename]}.{ext}"
            else:
                seen[filename] = 1
            zf.writestr(filename, content)

        manifest = {
            "format": "link-engine-ready",
            "cluster_id": cluster_id,
            "article_count": len(articles),
            "articles": [{
                "slug": a.get("slug"),
                "title": a.get("title"),
                "type": a.get("article_type"),
                "word_count": a.get("word_count"),
            } for a in articles],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
    buf.seek(0)
    return buf.read()


# ── CLI ──────────────────────────────────────────────────────────────────

def _cli():
    p = argparse.ArgumentParser(description="Export articles for the link engine")
    p.add_argument("--cluster", help="Export only this cluster_id")
    p.add_argument("--run", help="Export the cluster belonging to this run_id")
    p.add_argument("--out", default="link_engine_export",
                   help="Output directory (default: ./link_engine_export)")
    p.add_argument("--zip", action="store_true",
                   help="Also produce a .zip of each cluster")
    p.add_argument("--list", action="store_true",
                   help="List available clusters and exit")
    args = p.parse_args()

    if args.list:
        clusters = list_clusters()
        if not clusters:
            print("No clusters found.")
            return
        print(f"{'cluster_id':<40} {'articles':>8}  first_at")
        print("-" * 80)
        for c in clusters:
            print(f"{c['cluster_id']:<40} {c['n_articles']:>8}  {c.get('first_at','')[:19]}")
        return

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    # Resolve cluster IDs to export
    cluster_ids: list[str] = []
    if args.cluster:
        cluster_ids = [args.cluster]
    elif args.run:
        run = get_pipeline_run(args.run)
        if not run:
            print(f"ERROR: run {args.run} not found")
            sys.exit(1)
        cid = run.get("cluster_id")
        if not cid:
            print(f"ERROR: run {args.run} has no cluster_id")
            sys.exit(1)
        cluster_ids = [cid]
    else:
        cluster_ids = [c["cluster_id"] for c in list_clusters()]

    if not cluster_ids:
        print("No clusters to export.")
        return

    for cid in cluster_ids:
        cluster_dir = out_root / cid
        paths = export_cluster_to_dir(cid, cluster_dir)
        print(f"✓ {cid}: wrote {len(paths)} files → {cluster_dir}")

        if args.zip:
            zip_path = out_root / f"{cid}.zip"
            zip_path.write_bytes(export_cluster_to_zip(cid))
            print(f"  also: {zip_path}")

    print(f"\nDone. Upload the contents of {out_root}/<cluster_id>/ to the Streamlit dashboard.")


if __name__ == "__main__":
    _cli()


# ─────────────────────────────────────────────────────────────────────────
# ROUTES_TO_ADD — paste these into app.py for in-dashboard downloads
# ─────────────────────────────────────────────────────────────────────────
"""
At the top of app.py, add:

    from export_for_link_engine import export_cluster_to_zip, article_to_link_engine_md

Then add these routes (place near the existing /articles/.../download route):


@app.get("/runs/{run_id}/articles/download_for_link_engine")
async def download_for_link_engine(
    run_id: str,
    user: dict = Depends(require_user),
):
    '''Download all cluster articles in the EXACT format the link engine
    Streamlit dashboard expects (with frontmatter and cleaned bodies).'''
    from fastapi.responses import Response
    run = get_pipeline_run(run_id)
    if not run or not run.get("cluster_id"):
        raise HTTPException(400, "Run has no cluster_id")
    cluster_id = run["cluster_id"]
    zip_bytes = export_cluster_to_zip(cluster_id)
    filename = f"{cluster_id}-link-engine-ready.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/articles/{article_id}/download_for_link_engine")
async def download_one_for_link_engine(
    article_id: str,
    user: dict = Depends(require_user),
):
    '''Download a single article in link-engine format.'''
    from fastapi.responses import Response
    from db.sqlite_ops import get_article
    article = get_article(article_id)
    if not article:
        raise HTTPException(404, "Article not found")
    filename, content = article_to_link_engine_md(article)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
"""