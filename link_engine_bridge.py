"""
link_engine_bridge.py — connects the agent system to the link engine.

Two phases:
  1. cluster_pass(cluster_id): exports cluster articles to runs/<run_id>/interlink/cluster/,
     runs link_engine on that folder, returns matches list
  2. global_pass(cluster_id): exports cluster articles + all approved articles to
     runs/<run_id>/interlink/global/, runs link_engine, returns NEW matches only
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Optional

from db.sqlite_ops import get_articles_by_cluster, db_conn


PUBLISHED_DIR = Path("data/published_articles")  # global corpus lives here
PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


# ────────────────────────────────────────────────────────────────────────────
# Config loading: pull anthropic.api_key out of config.yaml
# ────────────────────────────────────────────────────────────────────────────
def _load_anthropic_key_from_config() -> Optional[str]:
    """Read anthropic.api_key from the project's root config.yaml."""
    # Prefer the existing config_loader if it exposes one
    try:
        from config_loader import load_config
        cfg = load_config() or {}
        key = (cfg.get("anthropic") or {}).get("api_key")
        if key and str(key).strip() and not str(key).startswith("..."):
            return str(key).strip()
    except Exception:
        pass

    # Fallback: parse YAML directly
    try:
        import yaml
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            key = (cfg.get("anthropic") or {}).get("api_key")
            if key and str(key).strip() and not str(key).startswith("..."):
                return str(key).strip()
    except Exception:
        pass

    return None


def _resolve_anthropic_key() -> Optional[str]:
    """Get the key from any source: env, config.yaml, or .env."""
    # 1. Already in environment
    key = (os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or "").strip()
    if key:
        return key

    # 2. config.yaml
    key = _load_anthropic_key_from_config()
    if key:
        return key

    # 3. Optional .env support if dotenv is installed
    try:
        from dotenv import load_dotenv
        load_dotenv()
        key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if key:
            return key
    except ImportError:
        pass

    return None


# ────────────────────────────────────────────────────────────────────────────
# Article export (unchanged)
# ────────────────────────────────────────────────────────────────────────────
def _to_md_file(article: dict, target_dir: Path) -> Path:
    """Write a single article to <target_dir>/<slug>.md with frontmatter."""
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = article["slug"] or f"untitled-{article['id'][:8]}"
    fm_lines = [
        "---",
        f'title: "{article["title"]}"',
        f"slug: {slug}",
        f"url: /{slug}",
    ]
    if article.get("article_type"):
        fm_lines.append(f"article_type: {article['article_type']}")
    if article.get("cluster_id"):
        fm_lines.append(f"cluster_id: {article['cluster_id']}")
    fm_lines.append("---\n")
    body = article.get("content_md") or ""
    path = target_dir / f"{slug}.md"
    path.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
    return path


def export_cluster(cluster_id: str, run_id: str) -> Path:
    """Export all WRITTEN articles in this cluster to a fresh folder."""
    base = Path("runs") / run_id / "interlink" / "cluster"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    articles = get_articles_by_cluster(cluster_id)
    written = [a for a in articles if (a.get("content_md") or "").strip()]
    for a in written:
        _to_md_file(a, base)
    print(f"[link_bridge] Exported {len(written)} cluster articles → {base}")
    return base


def export_global(cluster_id: str, run_id: str) -> Path:
    """Export cluster + all globally published articles to one folder."""
    base = Path("runs") / run_id / "interlink" / "global"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    articles = get_articles_by_cluster(cluster_id)
    for a in articles:
        if (a.get("content_md") or "").strip():
            _to_md_file(a, base)

    if PUBLISHED_DIR.exists():
        for src in PUBLISHED_DIR.glob("**/*.md"):
            shutil.copy2(src, base / src.name)

    print(f"[link_bridge] Exported global corpus → {base}")
    return base


# ────────────────────────────────────────────────────────────────────────────
# Subprocess invocation with API key injection
# ────────────────────────────────────────────────────────────────────────────
def _build_subprocess_env(db_path: Optional[str] = None) -> dict:
    """Build the env for the link_engine subprocess.

    Resolves the Anthropic API key from env / config.yaml / .env (in that order)
    and injects it as ANTHROPIC_API_KEY so the link_engine subprocess can use
    the standard Anthropic SDK without further config.
    """
    env = os.environ.copy()

    api_key = _resolve_anthropic_key()
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    if db_path:
        env["LINK_ENGINE_DB"] = db_path

    return env


def run_link_engine(corpus_dir: Path, db_path: Optional[str] = None) -> Dict:
    """
    Invoke the existing link_engine CLI on a directory.
    Returns parsed link_report.json.
    """
    env = _build_subprocess_env(db_path)

    if not env.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY could not be resolved. Checked: shell env, "
            "config.yaml (anthropic.api_key), and .env. Make sure your root "
            "config.yaml has a real anthropic.api_key (not a placeholder)."
        )

    cmd = [sys.executable, "-m", "link_engine.cli", "run", str(corpus_dir)]
    print(f"[link_bridge] Running: {' '.join(cmd)}")
    masked = env["ANTHROPIC_API_KEY"]
    print(f"[link_bridge] API key forwarded: {masked[:12]}…{masked[-4:]}")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        err = (proc.stderr or "") + "\n--- stdout ---\n" + (proc.stdout or "")
        raise RuntimeError(f"link_engine failed:\n{err[:2000]}")

    report_path = Path("output") / "link_report.json"
    if report_path.exists():
        return {"status": "ok", "report": json.loads(report_path.read_text(encoding="utf-8"))}
    return {"status": "ok", "report": []}


def cluster_pass(cluster_id: str, run_id: str) -> Dict:
    folder = export_cluster(cluster_id, run_id)
    db_path = str(Path("runs") / run_id / "interlink" / "cluster.db")
    return run_link_engine(folder, db_path=db_path)


def global_pass(cluster_id: str, run_id: str) -> Dict:
    folder = export_global(cluster_id, run_id)
    db_path = str(Path("runs") / run_id / "interlink" / "global.db")
    return run_link_engine(folder, db_path=db_path)


def publish_cluster(cluster_id: str) -> int:
    """
    Move the (already-interlinked) cluster articles into the global corpus folder
    so they're available for future global passes.
    """
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    articles = get_articles_by_cluster(cluster_id)
    count = 0
    for a in articles:
        if (a.get("content_md") or "").strip():
            _to_md_file(a, PUBLISHED_DIR)
            with db_conn() as conn:
                conn.execute("UPDATE articles SET status='published' WHERE id=?", (a["id"],))
                conn.commit()
            count += 1
    print(f"[link_bridge] Published {count} articles to global corpus")
    return count


# ────────────────────────────────────────────────────────────────────────────
# CLI for quick verification
# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Resolving Anthropic API key…")
    key = _resolve_anthropic_key()
    if key:
        print(f"  ✓ Found: {key[:12]}…{key[-4:]}  (length: {len(key)})")
    else:
        print("  ✗ NOT FOUND. Check config.yaml has anthropic.api_key with a real value.")