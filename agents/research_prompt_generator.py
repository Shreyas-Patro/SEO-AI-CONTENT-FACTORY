"""
agents/research_prompt_generator.py — v5 (reads from state)
"""

import json
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from agents.base import AgentBase


RPG_SYSTEM_PROMPT = """You are a senior research strategist for Canvas Homes, a Bangalore real estate platform.

Produce a SINGLE master research prompt for Perplexity Pro that covers every factual claim
needed across the entire content cluster.

The prompt MUST:
1. Demand citations from authoritative sources (RERA, BBMP, Knight Frank, JLL, etc.)
2. Demand freshness — facts from 2024-2026 unless historical
3. Structure into clear research sections matching articles
4. Demand source URLs in markdown format
5. Be ONE coherent prompt, MAKE SURE THE RESEARCH IS UPDATED AND RELEVANT FOR 2026 

Return JSON:
{
  "master_research_prompt": "full prompt string",
  "research_questions": [{"section": "...", "questions": ["..."]}],
  "freshness_requirements": {"default": "last 18 months"},
  "source_priority": ["domain1", "domain2"],
  "estimated_perplexity_queries": 1,
  "estimated_perplexity_cost_usd": 0.20
}

NO markdown fences. Return raw JSON only."""


class ResearchPromptGeneratorAgent(AgentBase):
    NAME = "research_prompt_generator"
    READS_STATE = [StateKeys.CLUSTER_PLAN, StateKeys.FAQ_PLAN]
    WRITES_STATE = [StateKeys.RESEARCH_PROMPT]
    OUTPUT_REQUIRED = ["master_research_prompt", "research_questions"]
    OUTPUT_NON_EMPTY = ["master_research_prompt", "research_questions"]

    def _validate_output(self, output):
        problems = super()._validate_output(output)
        prompt = output.get("master_research_prompt", "") if isinstance(output, dict) else ""
        if isinstance(prompt, str) and len(prompt) < 200:
            problems.append(f"master_research_prompt too short ({len(prompt)} chars)")
        return problems

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        topic = agent_input.get("topic") or state.topic or ""
        cluster_plan = (
            agent_input.get("cluster_plan")
            or state.get(StateKeys.CLUSTER_PLAN, {})
            or {}
        )
        faqs_by_article = (
            agent_input.get("faqs_by_article")
            or (state.get(StateKeys.FAQ_PLAN, {}) or {}).get("faqs_by_article", {})
            or {}
        )

        print(f"[{self.NAME}] Building research prompt for: {topic}")

        articles = cluster_plan.get("articles", [])
        compact_articles = []
        for a in articles:
            compact_articles.append({
                "title": a.get("title"),
                "type": a.get("type"),
                "primary_keyword": a.get("target_keywords", {}).get("primary") if isinstance(a.get("target_keywords"), dict) else "",
                "outline": a.get("outline", [])[:10],
            })

        all_faqs = []
        for art_id, faqs in faqs_by_article.items():
            for f in (faqs if isinstance(faqs, list) else []):
                all_faqs.append({
                    "question": f.get("question", ""),
                    "article_id": art_id,
                })

        prompt = f"""Build a master Perplexity research prompt for: "{topic}" (Bangalore real estate).

CONTENT CLUSTER ({len(articles)} articles):
{json.dumps(compact_articles, indent=2)[:6000]}

FAQs NEEDING ANSWERS ({len(all_faqs)} total):
{json.dumps(all_faqs[:30], indent=2)[:4000]}

Produce the master research prompt as JSON.
"""

        result = call_llm_json(
            prompt, system=RPG_SYSTEM_PROMPT, model_role="architect",
            max_tokens=8000,
            cache_namespace=f"{topic}:research_prompt{self._retry_suffix()}",
        )
        self._track_llm(result)

        output = result.get("parsed", {})
        output.setdefault("estimated_perplexity_queries", 1)
        output.setdefault("estimated_perplexity_cost_usd", 0.20)
        output.setdefault("freshness_requirements", {"default": "last 18 months"})
        output.setdefault("source_priority", [
            "rera.karnataka.gov.in", "bbmp.gov.in", "knightfrank.com",
            "magicbricks.com", "nobroker.in", "livemint.com",
        ])

        return output