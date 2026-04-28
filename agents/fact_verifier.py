"""
agents/fact_verifier.py — v5 (AgentBase)
"""

import json
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history, add_to_verification_queue
from llm import call_llm_json


class FactVerifierAgent(AgentBase):
    NAME = "fact_verifier"
    OUTPUT_REQUIRED = ["article_id", "fact_check_score"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        content = article.get("content_md", "")
        if not content:
            raise ValueError("Article has no content")

        print(f"[fact_verifier] Checking: {article['title']}")

        try:
            system = open("prompts/fact_verifier.md").read()
        except FileNotFoundError:
            system = "Extract and verify every factual claim. Return JSON with claims array and summary."

        # Cross-reference KB
        kb_context = ""
        try:
            from db.chroma_ops import search_facts
            facts = search_facts(article["title"], top_k=20)
            kb_context = "\n".join([f"- KB: {f['text']}" for f in facts])
        except Exception:
            pass

        prompt = f"""Verify factual claims in this article.

TITLE: {article['title']}
CONTENT:
{content[:6000]}

KNOWLEDGE BASE FACTS:
{kb_context or "No KB facts available."}

Extract and verify every claim. Return JSON."""

        result = call_llm_json(prompt, system=system, model_role="bulk", max_tokens=4096,
                               cache_namespace=f"{article_id}:fact_verifier")
        self._track_llm(result)

        verification = result.get("parsed", {})
        summary = verification.get("summary", {})
        score = summary.get("overall_score", 0.7)

        # Queue flagged claims
        for claim in verification.get("claims", []):
            if claim.get("status") in ("flagged", "unverifiable"):
                try:
                    add_to_verification_queue(
                        article_id=article_id,
                        claim_text=claim.get("claim_text", ""),
                        issue_type=claim.get("issue_type", "unverifiable"),
                        suggested_correction=claim.get("suggested_correction", ""),
                    )
                except Exception:
                    pass

        update_article(article_id, fact_check_score=score, current_stage="fact_verifier")
        add_article_history(article_id, "fact_verifier",
                           f"Score: {score}, Flagged: {summary.get('flagged', 0)}", "")

        print(f"  ✅ Score: {score}")
        return {"article_id": article_id, "fact_check_score": score, "verification": verification}