"""
agents/faq_architect.py — v5 (cluster-level FAQ generation)

Generates FAQs for ALL articles in a cluster in one pass.
This is cheaper than calling per-article.
"""

import json
from db.sqlite_ops import get_article, get_articles_by_cluster, update_article
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from agents.base import AgentBase


FAQ_SYSTEM_PROMPT = """You are an AEO (Answer Engine Optimization) specialist for Canvas Homes, a Bangalore real estate platform.

Generate 5-8 FAQs per article that are optimized for:
1. Google Featured Snippets (40-60 word answers)
2. Voice search (question starts with who/what/where/when/why/how)
3. AI answer engines (clear, factual, self-contained answers)

Each FAQ must:
- Be a real question people search for
- Have a concise, factual answer (40-60 words)
- Include at least one specific data point where possible
- Target a unique long-tail keyword

Return STRICT JSON:
{
  "faqs_by_article": {
    "article_id_1": [
      {"question": "What is...?", "answer": "...", "target_keyword": "..."}
    ]
  },
  "total_faqs": 50,
  "summary": "..."
}"""


class FAQArchitectAgent(AgentBase):
    NAME = "faq_architect"
    READS_STATE = [StateKeys.KEYWORD_MAP, StateKeys.CLUSTER_PLAN]
    WRITES_STATE = [StateKeys.FAQ_PLAN]
    OUTPUT_REQUIRED = ["faqs_by_article", "total_faqs"]
    OUTPUT_NON_EMPTY = ["faqs_by_article"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        cluster_id = agent_input.get("cluster_id") or self.cluster_id or state.cluster_id
        topic = agent_input.get("topic") or state.topic or ""

        print(f"[{self.NAME}] Generating FAQs for cluster {cluster_id}")

        # Get keyword data
        kw_map = (
            agent_input.get("keyword_map")
            or state.get(StateKeys.KEYWORD_MAP, {})
            or {}
        )
        kw_groups = kw_map.get("keyword_groups", [])

        # Get cluster plan
        cluster_plan = (
            agent_input.get("cluster_plan")
            or state.get(StateKeys.CLUSTER_PLAN, {})
            or {}
        )

        # Get articles from DB
        articles = get_articles_by_cluster(cluster_id) if cluster_id else []
        if not articles:
            # Try from cluster plan
            articles_from_plan = cluster_plan.get("articles", [])
            print(f"  No DB articles, using {len(articles_from_plan)} from plan")
        else:
            print(f"  Found {len(articles)} articles in DB")

        # Build compact article list for the prompt
        compact_articles = []
        for art in articles:
            outline = json.loads(art.get("outline", "[]") or "[]")
            keywords = json.loads(art.get("target_keywords", "{}") or "{}")
            compact_articles.append({
                "id": art["id"],
                "title": art["title"],
                "type": art["article_type"],
                "primary_keyword": keywords.get("primary", art["title"]) if isinstance(keywords, dict) else art["title"],
                "outline_preview": outline[:8],
            })

        prompt = f"""Generate FAQs for all articles in this content cluster about "{topic}".

ARTICLES ({len(compact_articles)}):
{json.dumps(compact_articles, indent=2)[:5000]}

KEYWORD GROUPS (top 5):
{json.dumps(kw_groups[:5], indent=2)[:3000]}

Generate 5-8 FAQs per article. Return JSON with faqs_by_article keyed by article ID.
"""

        result = call_llm_json(
            prompt, system=FAQ_SYSTEM_PROMPT, model_role="bulk",
            max_tokens=8000,
            cache_namespace=f"{topic}:faq_architect{self._retry_suffix()}",
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
                    print(f"  ⚠️  Could not save FAQs for {art_id}: {e}")

        print(f"  ✅ Generated {total_faqs} FAQs across {len(faqs_by_article)} articles")

        return {
            "faqs_by_article": faqs_by_article,
            "total_faqs": total_faqs,
            "total_articles": len(faqs_by_article),
            "summary": parsed.get("summary", ""),
        }