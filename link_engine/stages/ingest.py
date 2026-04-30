import json
from datetime import datetime
from hashlib import md5, sha256
from pathlib import Path
from typing import Optional

import frontmatter

from link_engine.db.models import Article, Error
from link_engine.stages.extract_phrases import extract_phrases_for_article


def compute_article_id(slug: str) -> str:
    return sha256(slug.encode()).hexdigest()


def compute_content_hash(body: str) -> str:
    return md5(body.encode()).hexdigest()


def ingest_file(file_path: Path, run_id: str, session) -> Optional[Article]:
    try:
        raw = file_path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)

        slug = post.metadata.get("slug") or file_path.stem
        title = post.metadata.get("title", file_path.stem)
        url = post.metadata.get("url", f"/{slug}")
        body = post.content

        article_id = compute_article_id(slug)
        content_hash = compute_content_hash(body)

        existing = session.get(Article, article_id)

        if existing:
            if existing.content_hash == content_hash:
                existing.status = "unchanged"
                # Cached extraction is reused automatically inside extract_phrases_for_article.
                # This only makes an LLM call if no prior extraction exists for this hash.
                if not existing.title_phrases_json:
                    extract_phrases_for_article(existing, body, session, run_id)
                return existing
            else:
                existing.status = "changed"
                existing.content_hash = content_hash
                existing.title = title
                existing.url = url
                existing.file_path = str(file_path.resolve())
                existing.frontmatter_json = json.dumps(dict(post.metadata))
                existing.last_ingested_at = datetime.utcnow()
                # Content changed — re-extract phrases (one LLM call)
                extract_phrases_for_article(existing, body, session, run_id)
                return existing
        else:
            article = Article(
                article_id=article_id,
                slug=slug,
                title=title,
                url=url,
                file_path=str(file_path.resolve()),
                content_hash=content_hash,
                frontmatter_json=json.dumps(dict(post.metadata)),
                status="new",
                last_ingested_at=datetime.utcnow(),
            )
            session.add(article)
            session.flush()
            # New article — one LLM call to extract phrases, then cached forever
            extract_phrases_for_article(article, body, session, run_id)
            return article

    except Exception as e:
        session.add(Error(
            run_id=run_id,
            stage="ingestion",
            error_type="ingestion_error",
            message=str(e),
            rerun_eligible=True,
        ))
        return None


def ingest_directory(directory: Path, run_id: str, session) -> dict:
    md_files = list(directory.glob("**/*.md"))
    results = {"new": [], "changed": [], "unchanged": [], "errors": 0}

    for file_path in md_files:
        article = ingest_file(file_path, run_id, session)
        if article is None:
            results["errors"] += 1
        elif article.status in ("new", "changed"):
            results[article.status].append(article)
        else:
            results["unchanged"].append(article)

    session.flush()
    return results