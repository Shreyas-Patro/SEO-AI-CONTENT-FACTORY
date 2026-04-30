"""
agents/faq_architect.py — v5.1 (generates FAQs for ALL articles reliably)
"""

import json
from db.sqlite_ops import get_articles_by_cluster, update_article
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from agents.base import AgentBase


FAQ_SYSTEM = """You are an AEO specialist for Canvas Homes, a Bangalore real estate platform.

You MUST generate 5-8 FAQs for EVERY article listed below. Do NOT skip any article.

Each FAQ must:
- Be a real question people search for (start with who/what/where/when/why/how)
- Have a factual answer of 40-60 words (featured snippet sweet spot)
- Include at least one specific data point where possible
- Target a unique long-tail keyword

CRITICAL: Your response MUST contain an entry for EVERY article_id provided.
If you have 12 articles, you must return 12 keys in faqs_by_article.

Return STRICT JSON only — no markdown, no commentary:
{
  "faqs_by_article": {
    "art-xxxxx": [
      {"question": "...", "answer": "...", "target_keyword": "..."}
    ]
  },
  "total_faqs": 72,
  "summary": "..."
}"""


class FAQArchitectAgent(AgentBase):
    NAME = "faq_architect"
    READS_STATE = [StateKeys.KEYWORD_MAP, StateKeys.CLUSTER_PLAN]
    WRITES_STATE = [StateKeys.FAQ_PLAN]
    OUTPUT_REQUIRED = ["faqs_by_article", "total_faqs"]
    OUTPUT_NON_EMPTY = ["faqs_by_article"]

    def _validate_output(self, output):
        problems = super()._validate_output(output)
        if isinstance(output, dict):
            fba = output.get("faqs_by_article", {})
            if isinstance(fba, dict) and len(fba) < 3:
                problems.append(
                    f"Only {len(fba)} articles have FAQs — expected at least 6. "
                    f"LLM likely truncated. Will retry with stronger prompt."
                )
        return problems

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        cluster_id = agent_input.get("cluster_id") or self.cluster_id or state.cluster_id
        topic = agent_input.get("topic") or state.topic or ""

        print(f"[{self.NAME}] Generating FAQs for cluster {cluster_id}")

        kw_map = (
            agent_input.get("keyword_map")
            or state.get(StateKeys.KEYWORD_MAP, {})
            or {}
        )
        kw_groups = kw_map.get("keyword_groups", [])

        cluster_plan = (
            agent_input.get("cluster_plan")
            or state.get(StateKeys.CLUSTER_PLAN, {})
            or {}
        )

        # Get articles from DB
        articles = get_articles_by_cluster(cluster_id) if cluster_id else []
        print(f"  Found {len(articles)} articles in DB")

        if not articles:
            return {"faqs_by_article": {}, "total_faqs": 0, "total_articles": 0, "summary": "No articles found"}

        # Build article list with IDs prominently displayed
        article_lines = []
        for art in articles:
            outline = json.loads(art.get("outline", "[]") or "[]")
            keywords = json.loads(art.get("target_keywords", "{}") or "{}")
            pk = keywords.get("primary", art["title"]) if isinstance(keywords, dict) else art["title"]
            article_lines.append(
                f'  - ID: "{art["id"]}" | Title: "{art["title"]}" | Type: {art["article_type"]} | Primary KW: "{pk}"'
            )

        articles_block = "\n".join(article_lines)

        # Retry-aware prompt
        retry_note = ""
        if self._retry_attempt > 1:
            retry_note = f"""

WARNING: YOUR PREVIOUS RESPONSE ONLY HAD FAQs FOR A FEW ARTICLES.
YOU MUST GENERATE FAQs FOR ALL {len(articles)} ARTICLES.
Every article ID listed below MUST appear as a key in faqs_by_article."""

        prompt = f"""Generate 5-8 FAQs for EACH of these {len(articles)} articles about "{topic}".

ARTICLES (you MUST generate FAQs for every single one):
{articles_block}

KEYWORD GROUPS for context:
{json.dumps(kw_groups[:5], indent=2, default=str)[:2000]}
{retry_note}

Return JSON with faqs_by_article containing ALL {len(articles)} article IDs as keys."""

        # Use architect model (Sonnet) for reliability on large structured output
        result = call_llm_json(
            prompt, system=FAQ_SYSTEM,
            model_role="architect",
            max_tokens=8000,
            cache_namespace=f"{topic}:faq_v2{self._retry_suffix()}",
        )
        self._track_llm(result)

        parsed = result.get("parsed", {})
        faqs_by_article = parsed.get("faqs_by_article", {})

        # Persist FAQs on each article in DB
        total_faqs = 0
        for art_id, faqs in faqs_by_article.items():
            if isinstance(faqs, list):
                total_faqs += len(faqs)
                try:
                    update_article(art_id, faq_json=json.dumps(faqs))
                except Exception as e:
                    print(f"  Warning: Could not save FAQs for {art_id}: {e}")

        print(f"  Done: {total_faqs} FAQs across {len(faqs_by_article)} articles")

        return {
            "faqs_by_article": faqs_by_article,
            "total_faqs": total_faqs,
            "total_articles": len(faqs_by_article),
            "summary": parsed.get("summary", f"{total_faqs} FAQs for {len(faqs_by_article)} articles"),
        }