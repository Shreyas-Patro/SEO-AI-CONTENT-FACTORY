"""
agents/rewriter.py — surgical fixes from auditor / verifier feedback.

Two modes:
  1. fact_fix: replace flagged claims with corrections
  2. brand_fix: rewrite flagged passages preserving facts and structure
"""
import json
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history
from llm import call_llm


REWRITER_SYSTEM = """You are a surgical content editor for Canvas Homes (Bangalore real estate).

You will be given:
1. The current article markdown
2. A list of specific issues to fix

Your job: produce a NEW version of the article with the issues fixed and EVERYTHING ELSE INTACT.

Rules:
- Preserve all H1/H2/H3 structure
- Preserve every internal link (the [anchor](/slug) format)
- Preserve all citations (the [Source: ...] format)
- Preserve the FAQ section verbatim if present
- Only change what the issues say to change
- Do NOT shorten the article — keep word count ≥ original
- Apply all fixes in one pass; return the FULL revised article

Brand voice: knowledgeable, conversational ("you/your"), Bangalore-native, every paragraph has a number/date/name/source, short paragraphs (max 3 sentences)."""


class RewriterAgent(AgentBase):
    NAME = "rewriter"
    OUTPUT_REQUIRED = ["article_id", "fixes_applied"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input["article_id"]
        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        content = article.get("content_md", "")
        if not content:
            raise ValueError(f"Article {article_id} has no content yet")

        fact_issues = agent_input.get("fact_issues", []) or []
        brand_issues = agent_input.get("brand_issues", []) or []
        readability_score = agent_input.get("readability_score", 0)
        brief = agent_input.get("rewrite_brief") or {}
        priorities = brief.get("priorities", [])
        priority_block = "\n".join(
    f"- [{p['severity'].upper()}] {p['dimension']}: {p['instruction']}"
    for p in priorities
)
        if not fact_issues and not brand_issues:
            print(f"[rewriter] No issues — skipping")
            return {"article_id": article_id, "fixes_applied": 0, "skipped": True}

        print(f"[rewriter] {article['title']} — "
              f"{len(fact_issues)} fact issues, {len(brand_issues)} brand issues")

        # ── Build the issues block ──
        issues_text = []
        for c in fact_issues[:15]:
            claim = c.get("claim_text", "")
            issue = c.get("issue_type", "?")
            fix = c.get("suggested_correction", "")
            issues_text.append(f"FACT ISSUE [{issue}]: \"{claim}\"\n  → fix: {fix or 'remove or correct with citation'}")

        for p in brand_issues[:15]:
            orig = p.get("original_text", "")
            dim = p.get("dimension", "?")
            rewrite = p.get("suggested_rewrite", "")
            issues_text.append(f"BRAND ISSUE [{dim}, score {p.get('score','?')}]: \"{orig}\"\n  → rewrite: {rewrite}")

        if readability_score and readability_score < 55:
            issues_text.append(
                f"READABILITY: Flesch reading ease is {readability_score} (target ≥55). "
                f"Shorten sentences, use plainer words. Aim for grade 10."
            )

        prompt = f"""Apply these fixes to the article below.

ARTICLE:
---
{content}
---

ISSUES TO FIX ({len(issues_text)} total):
{chr(10).join(issues_text)}

Return the COMPLETE revised article. Preserve all structure, links, citations, and FAQ section."""

        result = call_llm(
            prompt, system=REWRITER_SYSTEM, model_role="writer",
            max_tokens=12000, temperature=0.3, use_cache=False, cache_system=True,
        )
        self._track_llm(result)

        new_content = result["text"]
        new_word_count = len(new_content.split())

        update_article(
            article_id,
            content_md=new_content,
            word_count=new_word_count,
            current_stage="rewriter",
        )
        add_article_history(
            article_id, "rewriter",
            f"Applied {len(fact_issues)} fact + {len(brand_issues)} brand fixes",
            new_content[:500],
        )

        print(f"  ✅ Rewrite complete — {new_word_count} words")

        return {
            "article_id": article_id,
            "fixes_applied": len(fact_issues) + len(brand_issues),
            "new_word_count": new_word_count,
        }