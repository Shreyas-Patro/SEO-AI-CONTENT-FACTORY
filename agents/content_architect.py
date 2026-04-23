"""
Content Architect Agent
Designs hub-spoke-sub_spoke cluster structures from keyword data.
"""

import json
from db.sqlite_ops import (start_agent_run, complete_agent_run, fail_agent_run,
                           create_cluster, update_cluster, create_article)
from db.chroma_ops import search_facts
from db.graph_ops import load_graph, get_nodes_by_type
from llm import call_llm_json


def run_content_architect(seed_topic, keyword_data, cluster_id=None):
    run_id = start_agent_run("content_architect", cluster_id=cluster_id,
                             input_summary=f"Topic: {seed_topic}")
    try:
        print(f"\n[Content Architect] Designing cluster for: {seed_topic}")

        # Get relevant facts from knowledge base
        relevant_facts = search_facts(f"{seed_topic} Bangalore", top_k=20)
        facts_summary = [f"- {f['text'][:100]}" for f in relevant_facts]

        # Get existing articles to avoid duplication
        G = load_graph()
        existing_articles = get_nodes_by_type(G, "article")
        existing_titles = [a.get("label", "") for a in existing_articles]

        prompt_template = open("prompts/content_architect.md").read()
        prompt = f"""Design a content cluster for "{seed_topic}" in Bangalore.

KEYWORD STRATEGY:
{json.dumps(keyword_data.get("keyword_map", {}), indent=2)}

FACTS AVAILABLE IN KNOWLEDGE BASE ({len(relevant_facts)} relevant facts):
{chr(10).join(facts_summary[:15])}

EXISTING ARTICLES (avoid duplication):
{json.dumps(existing_titles[:20], indent=2)}

Design the full hub-spoke-sub_spoke-FAQ cluster with detailed outlines and linking plan.
"""
        result = call_llm_json(prompt, system=prompt_template, model_role="architect", max_tokens=8000)
        cluster_plan = result.get("parsed", {})

        # Create cluster in DB if not exists
        if not cluster_id:
            cluster_id = create_cluster(seed_topic, seed_topic)

        # Create article records
        articles = cluster_plan.get("articles", [])
        hub_ids, spoke_ids, faq_ids = [], [], []

        for article in articles:
            article_id = create_article(
                title=article.get("title", ""),
                slug=article.get("slug", ""),
                cluster_id=cluster_id,
                article_type=article.get("type", "spoke"),
                target_keywords=article.get("target_keywords", {}),
                outline=article.get("outline", []),
            )
            article["db_id"] = article_id

            if article.get("type") == "hub":
                hub_ids.append(article_id)
            elif article.get("type") == "faq":
                faq_ids.append(article_id)
            else:
                spoke_ids.append(article_id)

        # Update cluster with article IDs
        update_cluster(cluster_id,
                      hub_article_ids=json.dumps(hub_ids),
                      spoke_article_ids=json.dumps(spoke_ids),
                      faq_article_ids=json.dumps(faq_ids),
                      cluster_plan=json.dumps(cluster_plan),
                      keyword_map=json.dumps(keyword_data))

        output = {
            "cluster_id": cluster_id,
            "cluster_plan": cluster_plan,
            "articles_created": len(articles),
            "cost_usd": result.get("cost_usd", 0),
        }

        complete_agent_run(run_id,
                          output_summary=f"Created {len(articles)} articles ({len(hub_ids)} hubs, {len(spoke_ids)} spokes, {len(faq_ids)} FAQs)",
                          tokens_in=result.get("tokens_in", 0),
                          tokens_out=result.get("tokens_out", 0),
                          cost_usd=result.get("cost_usd", 0))

        print(f"  ✅ Content Architect complete. {len(articles)} articles planned.")
        return output

    except Exception as e:
        fail_agent_run(run_id, str(e))
        raise