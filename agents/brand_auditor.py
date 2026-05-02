"""
agents/brand_auditor.py — v6 (proper rubric)

Composite score = 0.4 * deterministic_signals + 0.6 * llm_rubric.

Deterministic signals (free, fast):
- Sentence length distribution → conversational
- Citation density → knowledgeable
- Bangalore-locality term frequency → bangalore_native
- Number density per paragraph → specific
- Em-dash count → cleanliness (penalty if found)
- "you/your" frequency → conversational
- Heading hierarchy → structure

LLM rubric scores 5 dimensions on 1-10 scale and returns flagged passages.
"""
import json
import re
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history
from llm import call_llm_json


# ─── Deterministic scoring ──────────────────────────────────────────

def _compute_readability(text):
    try:
        import textstat
        return {
            "flesch_kincaid": round(textstat.flesch_reading_ease(text), 1),
            "gunning_fog": round(textstat.gunning_fog(text), 1),
            "word_count": len(text.split()),
            "readability_grade": "PASS" if textstat.flesch_reading_ease(text) >= 55 else "NEEDS_IMPROVEMENT",
        }
    except ImportError:
        words = len(text.split())
        sentences = max(text.count('.') + text.count('!') + text.count('?'), 1)
        return {
            "flesch_kincaid": 0,
            "avg_sentence_length": round(words / sentences, 1),
            "word_count": words,
            "readability_grade": "UNKNOWN",
        }


BANGALORE_LOCAL_TERMS = [
    "locality", "auto", "namma", "metro", "BMTC", "BBMP", "RERA", "BWSSB", "BESCOM",
    "ORR", "Outer Ring Road", "NICE", "MG Road", "lakh", "crore", "khata",
    "society", "complex", "block", "phase", "sector", "main road",
]


def _deterministic_signals(content_md: str) -> dict:
    """Compute objective brand signals — no LLM needed."""
    text = content_md
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip() and not p.startswith("#")]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s for s in sentences if len(s.split()) > 2]

    word_count = len(text.split())
    if word_count == 0:
        return {"composite_signal_score": 0}

    # Conversational: % "you/your" usage + avg sentence length
    you_count = len(re.findall(r"\b(you|your|you're|you've|you'll)\b", text, re.IGNORECASE))
    you_density = (you_count / word_count) * 100  # %
    avg_sent_len = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
    conversational_score = min(10.0, (you_density * 4) + max(0, 10 - abs(avg_sent_len - 18) / 2))

    # Knowledgeable: citation density (per 1000 words)
    citations = len(re.findall(r"\[Source:[^\]]+\]", text)) + len(re.findall(r"\(https?://[^\)]+\)", text))
    citation_density = (citations / word_count) * 1000  # per 1000 words
    knowledgeable_score = min(10.0, citation_density * 2)  # 5 citations per 1000 words = 10/10

    # Specific: number/date/percentage density per paragraph
    para_with_data = 0
    for p in paragraphs:
        if re.search(r"\d|\b(?:lakh|crore|year|month)\b", p, re.IGNORECASE):
            para_with_data += 1
    specific_score = (para_with_data / max(len(paragraphs), 1)) * 10

    # Bangalore-native: local term frequency
    bn_hits = sum(
        len(re.findall(rf"\b{re.escape(term)}\b", text, re.IGNORECASE))
        for term in BANGALORE_LOCAL_TERMS
    )
    bn_density = (bn_hits / word_count) * 1000  # per 1000 words
    bangalore_score = min(10.0, bn_density * 1.2)

    # Helpful: ends with takeaway? has H2 questions?
    has_takeaway = bool(re.search(r"##\s+(takeaway|summary|in summary|key takeaways)", text, re.IGNORECASE))
    question_h2s = len(re.findall(r"^##\s+(?:What|How|Why|When|Where|Is|Should|Can|Do)\b", text, re.MULTILINE))
    helpful_score = min(10.0, (3 if has_takeaway else 0) + min(7, question_h2s * 1.5))

    # Penalties
    em_dashes = text.count("—")
    em_dash_penalty = min(3, em_dashes * 0.3)

    composite = (
        knowledgeable_score * 0.25
        + conversational_score * 0.20
        + bangalore_score * 0.20
        + helpful_score * 0.15
        + specific_score * 0.20
    ) - em_dash_penalty

    return {
        "knowledgeable_signal": round(knowledgeable_score, 2),
        "conversational_signal": round(conversational_score, 2),
        "bangalore_native_signal": round(bangalore_score, 2),
        "helpful_signal": round(helpful_score, 2),
        "specific_signal": round(specific_score, 2),
        "em_dashes_found": em_dashes,
        "citations_count": citations,
        "you_density_per_100": round(you_density, 2),
        "avg_sentence_length": round(avg_sent_len, 1),
        "bangalore_term_density_per_1000": round(bn_density, 2),
        "composite_signal_score": round(max(0.0, min(10.0, composite)), 2),
    }


SYSTEM_PROMPT = """You are the brand guardian for Canvas Homes (Bangalore real estate).

You are given:
1. The article content
2. DETERMINISTIC SIGNALS already computed (from rules — trust them)

Your job: validate the deterministic signals with editorial judgement and flag specific passages that need fixing.

For each dimension, score 1-10:
1. KNOWLEDGEABLE — claims backed by specific sources?
2. CONVERSATIONAL — uses "you/your", short paragraphs, friendly tone?
3. BANGALORE_NATIVE — uses locality, auto, lakh, crore, real landmarks?
4. HELPFUL — actionable advice, empowers decision-making?
5. SPECIFIC — every paragraph has a number/date/name?

For each FAILED passage (any dimension scoring below threshold), output:
- original_text (verbatim)
- dimension that fails
- score (1-10)
- issue (one sentence)
- suggested_rewrite (concrete replacement)

Composite score = average of 5 dimensions.

Return STRICT JSON:
{
  "scores": {"knowledgeable": 8, "conversational": 7, "bangalore_native": 6, "helpful": 9, "specific": 8},
  "composite_score": 7.6,
  "pass": true,
  "flagged_passages": [
    {"original_text": "...", "dimension": "specific", "score": 3, "issue": "...", "suggested_rewrite": "..."}
  ],
  "overall_feedback": "1-2 sentences"
}"""


class BrandAuditorAgent(AgentBase):
    NAME = "brand_auditor"
    OUTPUT_REQUIRED = ["article_id", "brand_score"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        content = article.get("content_md", "")
        print(f"[brand_auditor] Auditing: {article['title']}")

        # Part A: Deterministic signals (free)
        readability = _compute_readability(content)
        signals = _deterministic_signals(content)
        print(f"  Readability: {readability.get('flesch_kincaid','?')}")
        print(f"  Signals: composite={signals['composite_signal_score']}, em_dashes={signals['em_dashes_found']}, cites={signals['citations_count']}")

        # Part B: LLM editorial review (informed by signals)
        try:
            brand_voice = open("prompts/brand_voice.md", encoding="utf-8").read()
            llm_system = f"{SYSTEM_PROMPT}\n\nBRAND VOICE REFERENCE:\n{brand_voice}"
        except FileNotFoundError:
            llm_system = SYSTEM_PROMPT

        prompt = f"""Audit this article. Use the deterministic signals as your starting point and flag specific failing passages.

DETERMINISTIC SIGNALS (already computed):
{json.dumps(signals, indent=2)}

TITLE: {article['title']}
CONTENT:
{content[:6000]}

Score all 5 dimensions and flag specific failing passages with suggested rewrites."""

        result = call_llm_json(
            prompt, system=llm_system, model_role="bulk", max_tokens=4096,
            cache_namespace=f"{article_id}:brand_auditor"
        )
        self._track_llm(result)

        audit = result.get("parsed", {}) or {}
        llm_composite = audit.get("composite_score", 7.0)

        # Final composite: 40% deterministic, 60% LLM rubric
        final_composite = round(
            0.4 * signals["composite_signal_score"] + 0.6 * llm_composite,
            2,
        )

        update_article(
            article_id,
            readability_score=readability.get("flesch_kincaid", 0),
            brand_tone_score=final_composite,
            current_stage="brand_auditor",
        )
        add_article_history(
            article_id, "brand_auditor",
            f"Readability: {readability.get('flesch_kincaid','?')}, Brand: {final_composite} "
            f"(signals: {signals['composite_signal_score']}, LLM: {llm_composite})",
            "",
        )

        print(f"  Final brand score: {final_composite} (signals: {signals['composite_signal_score']}, LLM: {llm_composite})")

        needs_rewrite = (
            final_composite < 7.0
            or readability.get("flesch_kincaid", 0) < 55
            or signals["em_dashes_found"] > 0
            or len(audit.get("flagged_passages", [])) > 3
        )

        return {
            "article_id": article_id,
            "brand_score": final_composite,
            "llm_score": llm_composite,
            "deterministic_signals": signals,
            "readability": readability,
            "audit": audit,
            "needs_rewrite": needs_rewrite,
            "flagged_passages": audit.get("flagged_passages", []),
        }