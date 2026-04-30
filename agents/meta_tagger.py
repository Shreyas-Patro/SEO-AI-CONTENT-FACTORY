"""
agents/meta_tagger.py — v6 
"""

import json
from agents.base import AgentBase
from db.pipeline_state import PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history
from llm import call_llm_json


class MetaTaggerAgent(AgentBase):
    NAME = "meta_tagger"
    OUTPUT_REQUIRED = ["article_id", "meta"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"[meta_tagger] Tagging: {article['title']}")
        content = article.get("content_md", "")
        faqs = json.loads(article.get("faq_json", "[]") or "[]")

        try:
            system = open("prompts/meta_tagger.md").read()
        except FileNotFoundError:
            system = "Generate SEO meta tags and schema markup. Return JSON."

        prompt = f"""Generate complete SEO + AEO meta package for this article.

TITLE: {article['title']}
SLUG: {article['slug']}
TYPE: {article['article_type']}
KEYWORDS: {article.get('target_keywords', '{}')}
WORD COUNT: {article.get('word_count', 0)}

CONTENT (first 600 words):
{content[:2400]}

FAQS ({len(faqs)}):
{json.dumps(faqs[:8], indent=2)[:2500]}

Generate ALL of:
1. meta_title (50-60 chars, includes primary keyword + brand)
2. meta_description (150-160 chars, includes keyword + CTA)
3. og_title, og_description (slightly more shareable phrasing)
4. focus_keyword
5. keywords (10-12)
6. semantic_keywords (5-8 LSI phrases)
7. tags (8-10)
8. category if applicable (one of: Locality Guide, Property Type, Legal, Finance, Lifestyle, Infrastructure, Market Analysis)
9. target_audience
10. content_intent (informational|transactional|navigational|comparison)
11. schema_article (JSON-LD Article)
12. schema_faq (JSON-LD FAQPage from the article's FAQs)
13. schema_breadcrumb (Home > Bangalore > <article title>)
14. image_alt_suggestions (hero + 1 per main section)
15. key_takeaways (3-5 bullets summarizing the article's main insights for AI overview targeting)

Return STRICT JSON. No markdown fences."""
        result = call_llm_json(prompt, system=system, model_role="bulk", max_tokens=4096,
                               cache_namespace=f"{article_id}:meta_tagger{self._retry_suffix()}")
        self._track_llm(result)

        meta = result.get("parsed", {})
        update_article(article_id,
                       meta_title=meta.get("meta_title", ""),
                       meta_description=meta.get("meta_description", ""),
                       schema_json=json.dumps(meta),
                       current_stage="meta_tagger")
        add_article_history(article_id, "meta_tagger", "Meta tags generated", "")

        print(f"  ✅ Meta tagged")
        return {"article_id": article_id, "meta": meta}