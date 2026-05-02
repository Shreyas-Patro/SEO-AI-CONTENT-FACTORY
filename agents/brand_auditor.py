"""
agents/brand_auditor.py — v5 (AgentBase)
Two-part: algorithmic readability + LLM brand scoring.
"""

import json
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history
from llm import call_llm_json


def _compute_readability(text):
    """Free readability scoring — no LLM Called."""
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
            "readability_grade": "UNKNOWN (textstat not installed)",
        }


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

        # Part A: Free readability
        readability = _compute_readability(content)
        print(f"  Readability: {readability.get('flesch_kincaid', '?')}")

        # Part B: LLM brand scoring
        try:
            system = open("prompts/brand_auditor.md", encoding="utf-8").read()
            brand_voice = open("prompts/brand_voice.md", encoding="utf-8").read()
            system = f"{system}\n\nBRAND VOICE REFERENCE:\n{brand_voice}"
        except FileNotFoundError:
            system = "Score this article on brand voice compliance (1-10 each): knowledgeable, conversational, bangalore_native, helpful, specific. Return JSON."

        prompt = f"""Audit this article for brand voice.

TITLE: {article['title']}
CONTENT:
{content[:5000]}

Score all 5 dimensions and flag passages needing improvement."""

        result = call_llm_json(prompt, system=system, model_role="bulk", max_tokens=4096,
                               cache_namespace=f"{article_id}:brand_auditor")
        self._track_llm(result)

        audit = result.get("parsed", {})
        composite = audit.get("composite_score", 7.0)

        update_article(article_id, readability_score=readability.get("flesch_kincaid", 0),
                       brand_tone_score=composite, current_stage="brand_auditor")
        add_article_history(article_id, "brand_auditor",
                           f"Readability: {readability.get('flesch_kincaid', '?')}, Brand: {composite}", "")

        print(f"  ✅ Brand score: {composite}")
       # Compute needs_rewrite signal for the supervisor
        needs_rewrite = (
            composite < 7.0
            or readability.get("flesch_kincaid", 0) < 55
            or len(audit.get("flagged_passages", [])) > 3
        )

        return {
            "article_id": article_id,
            "brand_score": composite,
            "readability": readability,
            "audit": audit,
            "needs_rewrite": needs_rewrite,
            "flagged_passages": audit.get("flagged_passages", []),
        }