"""
agents/fact_verifier.py — v6 (chunked claim batching)
"""
import json
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import (
    get_article, update_article, add_article_history, add_to_verification_queue,
)
from llm import call_llm_json


def _chunk_article(content_md: str, target_chunks: int = 3) -> list:
    paragraphs = [p for p in content_md.split("\n\n") if p.strip()]
    if not paragraphs:
        return [content_md]
    per = max(1, len(paragraphs) // target_chunks)
    chunks = []
    for i in range(0, len(paragraphs), per):
        chunks.append("\n\n".join(paragraphs[i:i + per]))
    return chunks


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
            system = (
                "Extract every factual claim from the passage and verify each. "
                "Return JSON with claims[] and summary{verified, flagged, unverifiable, overall_score}."
            )

        # KB context (keep small — article body dominates the budget)
        kb_context = ""
        try:
            from db.chroma_ops import search_facts
            facts = search_facts(article["title"], top_k=15)
            kb_context = "\n".join([f"- KB: {f['text'][:200]}" for f in facts])
        except Exception:
            pass

        chunks = _chunk_article(content, target_chunks=3)
        all_claims = []
        chunk_scores = []

        for i, chunk in enumerate(chunks, 1):
            prompt = f"""Verify factual claims in this passage (chunk {i}/{len(chunks)}).

ARTICLE TITLE: {article['title']}

PASSAGE:
{chunk[:6000]}

KNOWLEDGE BASE CONTEXT:
{kb_context[:2000] or "(none)"}

Extract every claim, verify against KB or general knowledge, return JSON."""

            result = call_llm_json(
                prompt, system=system, model_role="bulk", max_tokens=4096,
                cache_namespace=f"{article_id}:fact_verifier:c{i}{self._retry_suffix()}",
            )
            self._track_llm(result)
            verification = result.get("parsed", {}) or {}
            for claim in verification.get("claims", []):
                all_claims.append(claim)
            chunk_scores.append(
                verification.get("summary", {}).get("overall_score", 0.7)
            )

        avg_score = sum(chunk_scores) / max(len(chunk_scores), 1) if chunk_scores else 0.7
        flagged = sum(1 for c in all_claims if c.get("status") == "flagged")
        unverifiable = sum(1 for c in all_claims if c.get("status") == "unverifiable")
        verified = sum(1 for c in all_claims if c.get("status") == "verified")

        # Queue flagged claims for human review
        for claim in all_claims:
            if claim.get("status") in ("flagged", "unverifiable"):
                try:
                    add_to_verification_queue(
                        article_id=article_id,
                        claim_text=claim.get("claim_text", ""),
                        issue_type=claim.get("issue_type") or claim.get("status"),
                        suggested_correction=claim.get("suggested_correction", ""),
                    )
                except Exception:
                    pass

        update_article(article_id, fact_check_score=avg_score, current_stage="fact_verifier")
        add_article_history(
            article_id, "fact_verifier",
            f"Score: {avg_score:.2f} | claims={len(all_claims)} flagged={flagged}", "",
        )

        verification_summary = {
            "total_claims": len(all_claims),
            "verified": verified,
            "flagged": flagged,
            "unverifiable": unverifiable,
            "overall_score": avg_score,
            "claims": all_claims,
        }

        print(f"  ✅ Score: {avg_score:.2f} | {verified} ok / {flagged} flagged / {unverifiable} unverifiable")

        return {
            "article_id": article_id,
            "fact_check_score": avg_score,
            "verification": verification_summary,
            "needs_rewrite": avg_score < 0.85 or flagged > 3,
        }