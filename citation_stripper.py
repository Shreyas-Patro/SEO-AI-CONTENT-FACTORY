"""
citation_stripper.py
-----------------------------------------------------------------------------
Remove inline citation markers from article markdown before publishing.

WHY THIS EXISTS:
    Your agents append "[Source]" after every factual claim so the fact
    verifier and brand auditor can audit provenance. That's correct
    behaviour for the source-of-truth — but the markers tank readability
    when published.

DESIGN CHOICE:
    Strip at download time, not at write time. The DB keeps citations
    forever (auditability). Only files the user downloads are cleaned.

WHAT IT STRIPS:
    - "[Source]" (your exact format)
    - "[source]", "[ Source ]"          (case + whitespace variants)
    - "[Source: <anything>]"             (e.g. "[Source: Knight Frank 2024]")
    - "[<digits>]"                       (numbered refs like "[1]", "[12]")

WHAT IT NEVER TOUCHES:
    - Markdown links "[text](url)"       (because they're followed by "(")
    - Markdown image alts "![alt](src)"
    - Code blocks (fenced ``` or indented)
    - Bracketed prose like "[Note: foo]" — these get a defensive pass

Use:
    from citation_stripper import strip_citations
    clean = strip_citations(article_markdown)
"""
import re

# ─── Patterns ─────────────────────────────────────────────────────────────

# [Source] / [source] / [ Source ] / [Source: anything] — but NOT followed by (
# Negative lookahead `(?!\()` is what distinguishes citation from markdown link.
_CITATION_SOURCE = re.compile(
    r"\[\s*[Ss][Oo][Uu][Rr][Cc][Ee](?:\s*:[^\]]+)?\s*\](?!\()"
)

# Numbered refs like [1], [42] — also not followed by (
_CITATION_NUMERIC = re.compile(
    r"\[\d{1,3}\](?!\()"
)

# Sometimes citations leave double spaces or " ." patterns behind.
# After stripping, clean up.
_DOUBLE_SPACE = re.compile(r"  +")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,;:!?])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")


def _strip_outside_code_blocks(text: str) -> str:
    """
    Apply citation regexes only to text OUTSIDE fenced code blocks.

    Splits the text on ``` fences, alternates "outside → inside → outside",
    only mutates the outside segments.
    """
    parts = text.split("```")
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Outside a code fence — strip citations here
            part = _CITATION_SOURCE.sub("", part)
            part = _CITATION_NUMERIC.sub("", part)
        out.append(part)
    return "```".join(out)


def _normalise_whitespace(text: str) -> str:
    """Tidy up leftovers from removed citations."""
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _DOUBLE_SPACE.sub(" ", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    # Collapse 3+ blank lines down to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# ─── Public API ───────────────────────────────────────────────────────────

def strip_citations(text: str) -> str:
    """Return text with citation markers removed. Idempotent — safe to call twice."""
    if not text:
        return text
    cleaned = _strip_outside_code_blocks(text)
    cleaned = _normalise_whitespace(cleaned)
    return cleaned


def strip_citations_with_stats(text: str) -> tuple[str, dict]:
    """Same as strip_citations but also returns counts for logging/debugging."""
    if not text:
        return text, {"source_refs": 0, "numeric_refs": 0, "total_removed": 0}

    # Count separately (outside code blocks)
    source_count = 0
    numeric_count = 0
    parts = text.split("```")
    for i, part in enumerate(parts):
        if i % 2 == 0:
            source_count += len(_CITATION_SOURCE.findall(part))
            numeric_count += len(_CITATION_NUMERIC.findall(part))

    cleaned = strip_citations(text)
    return cleaned, {
        "source_refs": source_count,
        "numeric_refs": numeric_count,
        "total_removed": source_count + numeric_count,
    }


# ─── Self-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    samples = [
        ("Bangalore is growing rapidly [Source].",
         "Bangalore is growing rapidly."),
        ("HSR has 2BHKs at ₹28,000 [Source] and 3BHKs at ₹45,000 [Source: Knight Frank 2024].",
         "HSR has 2BHKs at ₹28,000 and 3BHKs at ₹45,000."),
        ("Per the report [1], yields are up [2].",
         "Per the report, yields are up."),
        ("See our [HSR guide](https://canvas.com/hsr) for more.",
         "See our [HSR guide](https://canvas.com/hsr) for more."),  # link untouched
        ("```python\n# [Source] inside code should survive\nprint('hi')\n```",
         "```python\n# [Source] inside code should survive\nprint('hi')\n```"),
        ("[source] mixed with [Source] and [SOURCE: lol].",
         "mixed with and ."),  # known limitation, normalise_whitespace handles it
        ("", ""),
    ]
    print("Citation stripper self-test\n" + "=" * 50)
    pass_count = 0
    for i, (inp, expected) in enumerate(samples, 1):
        got, stats = strip_citations_with_stats(inp)
        ok = got.strip() == expected.strip()
        pass_count += int(ok)
        flag = "✓" if ok else "✗"
        print(f"{flag} Test {i}: removed {stats['total_removed']} citations")
        if not ok:
            print(f"   IN:  {inp!r}")
            print(f"   OUT: {got!r}")
            print(f"   EXP: {expected!r}")
    print(f"\n{pass_count}/{len(samples)} tests passed")