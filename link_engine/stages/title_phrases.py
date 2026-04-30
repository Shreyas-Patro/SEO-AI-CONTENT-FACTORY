"""
Derive long-tail anchor-candidate phrases from an article title.

Handles:
  - Titles with em-dash or colon subtitles ("Food in HSR Layout — The Complete Dining Guide")
  - Leading prefixes ("How to", "Guide to")
  - Trailing boilerplate suffixes ("Complete Guide", "Honest Assessment")
  - Multi-part titles — each side of a dash/colon is a candidate
"""
import json
import re
from typing import List

from link_engine.config import get_config


STOPWORD_STARTS = {"a", "an", "the", "and", "or"}

# Common trailing boilerplate that doesn't belong in an anchor.
# Stripped from the END of phrases. Matched case-insensitively.
TRAILING_BOILERPLATE = [
    "the complete guide",
    "a complete guide",
    "the complete dining guide",
    "the complete activity guide",
    "an honest assessment",
    "honest assessment",
    "complete guide",
    "full guide",
    "ultimate guide",
    "where to go after dark",
    "everything you need to know",
    "step by step",
    "in detail",
    "explained",
]


def _strip_prefix(text: str, prefixes: List[str]) -> str:
    t = text.strip()
    lower = t.lower()
    for prefix in prefixes:
        p = prefix.lower().strip()
        if lower.startswith(p + " "):
            return t[len(prefix):].strip()
    return t


def _strip_trailing(text: str) -> str:
    """Remove trailing boilerplate like 'The Complete Guide'."""
    t = text.strip()
    lower = t.lower()
    for suffix in sorted(TRAILING_BOILERPLATE, key=len, reverse=True):
        if lower.endswith(" " + suffix):
            t = t[: -(len(suffix) + 1)].strip()
            lower = t.lower()
    return t


def _clean(phrase: str) -> str:
    phrase = re.sub(r"\s+", " ", phrase).strip()
    phrase = phrase.strip("—-–:,;.!?")
    return phrase.strip()


def _split_on_separators(title: str) -> List[str]:
    """Split title on em-dash, en-dash, hyphen-with-spaces, or colon."""
    normalised = re.sub(r"\s+[—–\-:]\s+", "|", title)
    parts = [p.strip() for p in normalised.split("|") if p.strip()]
    return parts if len(parts) > 1 else [title]


def _is_usable(phrase: str, min_words: int, max_words: int) -> bool:
    words = phrase.split()
    if not (min_words <= len(words) <= max_words):
        return False
    if words[0].lower() in STOPWORD_STARTS:
        return False
    return True


def derive_title_phrases(title: str) -> List[str]:
    """Return lowercased candidate anchor phrases for a given title."""
    cfg = get_config()
    prefixes = cfg.get("title_prefix_strip", [])
    min_words = cfg.get("anchor_min_words", 3)
    max_words = cfg.get("anchor_max_words", 8)

    if not title:
        return []

    raw = _clean(title)
    candidates = []

    # Candidate 1: whole title, strip leading prefix and trailing boilerplate
    full = _strip_trailing(_strip_prefix(raw, prefixes))
    candidates.append(full.lower())

    # Candidate 2+: each side of em-dash / colon splits, each cleaned
    parts = _split_on_separators(raw)
    for part in parts:
        stripped = _strip_trailing(_strip_prefix(part, prefixes))
        if stripped:
            candidates.append(stripped.lower())

    # Candidate: original raw title lowercased (fallback)
    candidates.append(raw.lower())

    # De-dupe, keep order, filter by usable length
    seen = set()
    usable = []
    for c in candidates:
        c = _clean(c).lower()
        if not c or c in seen:
            continue
        seen.add(c)
        if _is_usable(c, min_words, max_words):
            usable.append(c)

    # Order: longest first (most specific), but cap at 5 candidates
    usable.sort(key=lambda s: -len(s.split()))
    return usable[:5]


def serialize_phrases(phrases: List[str]) -> str:
    return json.dumps(phrases)


def deserialize_phrases(blob: str) -> List[str]:
    if not blob:
        return []
    try:
        return json.loads(blob)
    except Exception:
        return []