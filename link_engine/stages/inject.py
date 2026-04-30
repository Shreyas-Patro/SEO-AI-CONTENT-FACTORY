import re
import shutil
from datetime import datetime
from hashlib import md5
from pathlib import Path

import frontmatter

from link_engine.config import get_config
from link_engine.db.models import Error, Injection


def _is_inside_code_block(text: str, pos: int) -> bool:
    return text[:pos].count("```") % 2 == 1


def _is_inside_link(text: str, pos: int) -> bool:
    for m in re.finditer(r'\[([^\]]+)\]\([^)]+\)', text):
        if m.start() <= pos <= m.end():
            return True
    return False


def _slugify(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text.strip('-')


def inject_approved_links(approved_anchors, session, run_id: str, dry_run: bool = False) -> dict:
    cfg = get_config()
    buffer_chars = cfg.get("injection_buffer_chars", 150)

    results = {"injected": 0, "errors": 0, "skipped": 0}

    # Group by source article
    by_article = {}
    for anchor in approved_anchors:
        article_id = anchor.match.source_chunk.article_id
        by_article.setdefault(article_id, []).append(anchor)

    for article_id, anchors in by_article.items():
        article = anchors[0].match.source_chunk.article
        file_path = Path(article.file_path)

        raw = file_path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        current_hash = md5(post.content.encode()).hexdigest()

        if current_hash != article.content_hash:
            for anchor in anchors:
                _record_error(session, anchor, run_id,
                              "file_changed",
                              "File changed since ingest — aborting injection")
                results["errors"] += 1
            continue

        body = post.content
        frontmatter_offset = raw.index(body)

        backup_path = file_path.with_suffix(".md.bak")
        shutil.copy2(file_path, backup_path)

        try:
            # Sort anchors by absolute position DESCENDING so earlier
            # injections don't shift later offsets
            anchors_sorted = sorted(
                anchors,
                key=lambda a: a.match.source_chunk.char_start + (a.match.phrase_char_start or 0),
                reverse=True,
            )

            content = raw
            injected_in_file = 0

            for anchor in anchors_sorted:
                match = anchor.match
                chunk = match.source_chunk
                anchor_text = anchor.edited_anchor or anchor.anchor_text or match.matched_phrase
                target_article = match.target_chunk.article
                target_url = target_article.url

                # Absolute offset in file = frontmatter length + chunk offset + phrase offset
                if match.phrase_char_start is None:
                    _record_error(session, anchor, run_id,
                                  "missing_offset",
                                  "Match is missing phrase_char_start")
                    results["errors"] += 1
                    continue

                absolute_pos = frontmatter_offset + chunk.char_start + match.phrase_char_start

                # Verify the phrase still sits at that position
                # (the file hash check already protects us, this is a belt-and-braces)
                expected = match.matched_phrase
                actual = content[absolute_pos:absolute_pos + len(expected)]
                if actual != expected:
                    # Try case-insensitive fallback within this chunk's range
                    chunk_start = frontmatter_offset + chunk.char_start
                    chunk_end = frontmatter_offset + chunk.char_end
                    region = content[chunk_start:chunk_end]
                    idx = region.lower().find(expected.lower())
                    if idx == -1:
                        _record_error(session, anchor, run_id,
                                      "phrase_not_found",
                                      f"Phrase '{expected}' not found at expected offset")
                        results["errors"] += 1
                        continue
                    absolute_pos = chunk_start + idx
                    actual = content[absolute_pos:absolute_pos + len(expected)]

                if absolute_pos < buffer_chars:
                    _record_error(session, anchor, run_id,
                                  "too_close_to_start",
                                  "Injection position within buffer zone")
                    results["skipped"] += 1
                    continue
                if _is_inside_code_block(content, absolute_pos):
                    _record_error(session, anchor, run_id,
                                  "inside_code_block",
                                  "Position inside fenced code block")
                    results["skipped"] += 1
                    continue
                if _is_inside_link(content, absolute_pos):
                    _record_error(session, anchor, run_id,
                                  "inside_existing_link",
                                  "Position inside existing markdown link")
                    results["skipped"] += 1
                    continue

                # The link points to the target article. If you want section anchoring,
                # uncomment the slug logic below.
                link_target = target_url
                # target_heading = match.target_chunk.heading or ""
                # target_slug = _slugify(target_heading)
                # if target_slug:
                #     link_target = f"{target_url}#{target_slug}"

                # Use the source-cased phrase as the visible anchor text
                visible_anchor = actual
                link_md = f"[{visible_anchor}]({link_target})"

                if not dry_run:
                    content = (
                        content[:absolute_pos]
                        + link_md
                        + content[absolute_pos + len(visible_anchor):]
                    )

                session.add(Injection(
                    anchor_id=anchor.anchor_id,
                    run_id=run_id,
                    status="injected" if not dry_run else "skipped",
                    injected_at=datetime.utcnow() if not dry_run else None,
                    backup_path=str(backup_path),
                ))
                injected_in_file += 1
                results["injected"] += 1

            if not dry_run and injected_in_file > 0:
                file_path.write_text(content, encoding="utf-8")

        except Exception as e:
            shutil.copy2(backup_path, file_path)
            for anchor in anchors:
                _record_error(session, anchor, run_id, "injection_error", str(e))
                results["errors"] += 1

    session.flush()
    return results


def _record_error(session, anchor, run_id, error_type, message):
    session.add(Injection(
        anchor_id=anchor.anchor_id,
        run_id=run_id,
        status="injection_error",
        error_message=message,
    ))
    session.add(Error(
        run_id=run_id,
        stage="injection",
        article_id=anchor.match.source_chunk.article_id,
        chunk_id=anchor.match.source_chunk.chunk_id,
        match_id=anchor.match_id,
        error_type=error_type,
        message=message,
        rerun_eligible=True,
    ))