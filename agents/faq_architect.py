"""
FAQ Architect Agent
Generates FAQs for each article, optimized for AEO/featured snippets.
"""

import json
from db.sqlite_ops import (start_agent_run, complete_agent_run, fail_agent_run,
                           get_article, update_article)
from llm import call_llm_json


def run_faq_architect(article_id, keyword_data, cluster_id=None):
    run_id = start_agent_run("faq_architect", cluster_id=cluster_id,
                             article_id=article_id, input_summary=f"Article: {article_id}")
    try:
        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"\n[FAQ Architect] Generating FAQs for: {article['title']}")

        prompt_template = open("prompts/faq_architect.md").read()
        prompt = f"""Generate FAQs for this article:

ARTICLE TITLE: {article['title']}
ARTICLE TYPE: {article['article_type']}
ARTICLE OUTLINE:
{json.dumps(json.loads(article.get('outline', '[]')), indent=2)}

TARGET KEYWORDS:
{json.dumps(json.loads(article.get('target_keywords', '{}')), indent=2)}

AVAILABLE KEYWORD DATA:
{json.dumps(keyword_data.get("keyword_map", {}).get("keyword_groups", [])[:5], indent=2)}

Generate 5-10 FAQs that match real search queries and are optimized for featured snippets.
"""
        result = call_llm_json(prompt, system=prompt_template, model_role="bulk")
        faq_data = result.get("parsed", {})

        # Store FAQs in article
        faqs = faq_data.get("faqs", [])
        update_article(article_id, faq_json=json.dumps(faqs))

        complete_agent_run(run_id,
                          output_summary=f"Generated {len(faqs)} FAQs",
                          tokens_in=result.get("tokens_in", 0),
                          tokens_out=result.get("tokens_out", 0),
                          cost_usd=result.get("cost_usd", 0))

        print(f"  ✅ FAQ Architect complete. {len(faqs)} FAQs generated.")
        return {"article_id": article_id, "faqs": faqs, "cost_usd": result.get("cost_usd", 0)}

    except Exception as e:
        fail_agent_run(run_id, str(e))
        raise