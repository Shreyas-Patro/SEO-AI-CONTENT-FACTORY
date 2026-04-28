"""
agents/meta_tagger.py — v5 (AgentBase)
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

        prompt = f"""Generate meta tags for this article.

TITLE: {article['title']}
SLUG: {article['slug']}
TYPE: {article['article_type']}
KEYWORDS: {article.get('target_keywords', '{}')}
WORD COUNT: {article.get('word_count', 0)}

CONTENT (first 500 words):
{content[:2000]}

FAQS ({len(faqs)}):
{json.dumps(faqs[:5], indent=2)[:1500]}
"""
        result = call_llm_json(prompt, system=system, model_role="bulk", max_tokens=4096,
                               cache_namespace=f"{article_id}:meta_tagger")
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