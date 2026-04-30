"""
Auto-extract canonical anchor phrases from article content using an LLM.

Runs once per article per content version. Cached by content_hash — if the
article hasn't changed, no API call is made.

For a 500-article site, one-time cost is ~$2.50. Adding new articles costs
only for those new articles. Re-running the pipeline is free for unchanged content.
"""
import json
import time
from hashlib import md5

import anthropic

from link_engine.config import get_config
from link_engine.db.models import Article, Error


SYSTEM_PROMPT = """You extract canonical anchor-text phrases from blog articles.

Given an article's title and body, return 5 to 10 short phrases that another article could naturally use as link text to reference this article. Your output is used to automatically build internal links across a blog.

RULES for each phrase:
1. It must be something that would naturally appear inside flowing prose in another article — not a question, not a full sentence.
2. Length: 1 to 7 words.
3. Prefer the canonical name of the topic (e.g. "SUV", "kitchen renovation cost", "REST API design").
4. Include both short forms AND one longer long-tail form when both are natural.
5. Include plural and singular forms if both would appear in writing.
6. No generic phrases: never "learn more", "click here", "this guide", "read about", "more info".
7. No phrases that are just filler words — must have real topic content.

Respond with a single line of JSON only, no markdown fences, no explanation:
{"phrases": ["phrase one", "phrase two", "..."]}"""


def compute_extraction_cache_key(content_hash: str, model: str) -> str:
    return md5(f"extract_v1:{model}:{content_hash}".encode()).hexdigest()


def _call_llm_for_phrases(title: str, body: str, cfg: dict) -> list:
    client = anthropic.Anthropic(api_key=cfg["anthropic_api_key"])
    model = cfg.get("llm_model", "claude-sonnet-4-20250514")

    # Truncate body if very long — first ~4000 chars capture the topic cleanly
    body_sample = body[:4000]

    user_prompt = f"""TITLE: {title}

BODY:
---
{body_sample}
---

Extract 5-10 canonical anchor phrases for this article."""

    last_error = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            data = json.loads(raw)
            phrases = data.get("phrases", [])
            # Clean and lowercase — keep original casing preserved by case-insensitive match in match.py
            cleaned = []
            seen = set()
            for p in phrases:
                if not isinstance(p, str):
                    continue
                p = p.strip().lower()
                if not p or p in seen:
                    continue
                words = p.split()
                if not (1 <= len(words) <= 7):
                    continue
                seen.add(p)
                cleaned.append(p)
            return cleaned

        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"LLM phrase extraction failed: {last_error}")


def extract_phrases_for_article(article: Article, body: str, session, run_id: str = None) -> list:
    """
    Extract or reuse cached phrases for an article. Returns a list of phrases.

    Caching logic:
      - If the article already has title_phrases_json AND the current content_hash
        is embedded in the cache marker, reuse them (no LLM call).
      - Otherwise, call the LLM and persist the result.
    """
    cfg = get_config()
    model = cfg.get("llm_model", "claude-sonnet-4-20250514")
    cache_key = compute_extraction_cache_key(article.content_hash, model)

    # Reuse existing phrases if the cache marker matches
    if article.title_phrases_json:
        try:
            wrapper = json.loads(article.title_phrases_json)
            if isinstance(wrapper, dict) and wrapper.get("cache_key") == cache_key:
                return wrapper.get("phrases", [])
        except Exception:
            pass  # fall through to re-extract

    # Need to extract fresh
    try:
        phrases = _call_llm_for_phrases(article.title or "", body, cfg)
    except Exception as e:
        session.add(Error(
            run_id=run_id,
            stage="ingestion",
            article_id=article.article_id,
            error_type="phrase_extraction_error",
            message=str(e),
            rerun_eligible=True,
        ))
        # Fall back to title-only phrases
        from link_engine.stages.title_phrases import derive_title_phrases
        phrases = derive_title_phrases(article.title or "")

    # Persist with cache marker
    wrapper = {"cache_key": cache_key, "phrases": phrases}
    article.title_phrases_json = json.dumps(wrapper)
    return phrases


def get_phrases_for_article(article: Article) -> list:
    """Read phrases back out of the wrapper format (used by match.py)."""
    if not article.title_phrases_json:
        return []
    try:
        wrapper = json.loads(article.title_phrases_json)
        if isinstance(wrapper, dict):
            return wrapper.get("phrases", [])
        # Backward compat — if it's a bare list from old ingests
        if isinstance(wrapper, list):
            return wrapper
    except Exception:
        pass
    return []