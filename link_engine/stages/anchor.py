"""
Anchor stage — now a LLM-based CONFIDENCE SCORER, not a generator.

The match already contains:
  - matched_phrase: the exact anchor text, as it appears in the source
  - target_article: the article the link will point to

All this stage does is ask the LLM: "Would a reader genuinely benefit from
this link, or is this a weak/forced connection?" and store the confidence
score for the review UI.
"""
import json
import time
from hashlib import md5

import anthropic

from link_engine.config import get_config
from link_engine.db.models import Anchor, Error, Match

SYSTEM_PROMPT = """You are evaluating whether an internal blog link makes sense.

You will be shown:
  - A SOURCE passage
  - A PROPOSED ANCHOR PHRASE (already present verbatim in the source)
  - The TARGET article's title and intro

Your job: score how useful this link would be for a reader.

A GOOD link (score 4-5):
- The target article directly expands on what the anchor phrase introduces
- A reader curious about the anchor phrase would genuinely learn more from the target
- The two topics are tightly related, not just sharing a keyword

A WEAK link (score 1-2):
- Keyword overlap only — the target is about a different angle or domain
- Example: source mentions "encryption" in cybersecurity context, target is about "encryption in blockchain"
- Example: source mentions "AI" generally, target is about "AI in healthcare specifically"
- The reader would click expecting one thing and get another

An OKAY link (score 3): topically related but not a strong must-click.

Respond with a single line of JSON only, no markdown:
{"confidence": 4, "reasoning": "one short sentence explaining the reader benefit or the mismatch"}"""


def compute_anchor_cache_key(source_hash: str, target_article_id: str, matched_phrase: str) -> str:
    return md5(f"eval_v1:{source_hash}:{target_article_id}:{matched_phrase.lower()}".encode()).hexdigest()


def evaluate_match(match: Match, session, run_id: str = None) -> bool:
    cfg = get_config()
    source_chunk = match.source_chunk
    target_chunk = match.target_chunk
    target_article = target_chunk.article

    cache_key = compute_anchor_cache_key(
        source_chunk.chunk_hash,
        target_article.article_id,
        match.matched_phrase or "",
    )

    # Cache hit
    existing = session.query(Anchor).filter_by(cache_key=cache_key).first()
    if existing:
        existing.match_id = match.match_id
        match.status = "anchor_ready"
        return True

    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    model = cfg.get("llm_model", "claude-sonnet-4-20250514")
    min_confidence = cfg.get("llm_confidence_threshold", 3)

    # Build target intro: title + first chunk text
    target_intro = target_chunk.text[:1200]

    user_prompt = f"""SOURCE PASSAGE:
---
{source_chunk.text}
---

PROPOSED ANCHOR PHRASE (present verbatim in source above): "{match.matched_phrase}"

TARGET ARTICLE TITLE: {target_article.title}
TARGET ARTICLE INTRO:
---
{target_intro}
---

Score whether a reader clicking "{match.matched_phrase}" in the source would genuinely benefit from landing on the target article."""

    last_error = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            data = json.loads(raw)
            confidence = int(data.get("confidence", 1))
            reasoning = data.get("reasoning", "")

            if confidence < min_confidence:
                match.status = "anchor_error"
                session.add(Error(
                    run_id=run_id,
                    stage="anchor",
                    article_id=source_chunk.article_id,
                    chunk_id=source_chunk.chunk_id,
                    match_id=match.match_id,
                    error_type="low_confidence",
                    message=f"Confidence {confidence} below threshold {min_confidence}: {reasoning}",
                    rerun_eligible=False,
                ))
                return False

            anchor = Anchor(
                match_id=match.match_id,
                anchor_text=match.matched_phrase,   # THE ANCHOR IS THE MATCHED PHRASE
                reasoning=reasoning,
                llm_confidence=confidence,
                model=model,
                cache_key=cache_key,
                status="pending_review",
            )
            session.add(anchor)
            match.status = "anchor_ready"
            return True

        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(2 ** attempt)

    match.status = "anchor_error"
    session.add(Error(
        run_id=run_id,
        stage="anchor",
        article_id=source_chunk.article_id,
        chunk_id=source_chunk.chunk_id,
        match_id=match.match_id,
        error_type="anchor_error",
        message=str(last_error),
        rerun_eligible=True,
    ))
    return False


def generate_all_anchors(session, run_id: str = None) -> dict:
    """
    Name kept as `generate_all_anchors` for CLI compatibility, but this now
    evaluates matches rather than generating anchor text.
    """
    matches = session.query(Match).filter_by(status="pending_anchor").all()
    results = {"success": 0, "errors": 0}

    for match in matches:
        if not match.source_chunk or not match.target_chunk:
            continue
        if not match.matched_phrase:
            # Shouldn't happen with new match.py, but be defensive
            match.status = "anchor_error"
            results["errors"] += 1
            continue
        if evaluate_match(match, session, run_id):
            results["success"] += 1
        else:
            results["errors"] += 1

    session.flush()
    return results


# Backwards-compatible alias in case other modules still import it
generate_anchor = evaluate_match