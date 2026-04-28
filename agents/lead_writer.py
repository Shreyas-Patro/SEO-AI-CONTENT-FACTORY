"""
agents/lead_writer.py — v5 (AgentBase)
Writes individual articles using knowledge base + brand voice.
"""

import json
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history, get_articles_by_cluster
from llm import call_llm


class LeadWriterAgent(AgentBase):
    NAME = "lead_writer"
    READS_STATE = [StateKeys.CLUSTER_PLAN]
    OUTPUT_REQUIRED = ["article_id", "word_count"]
    OUTPUT_NON_EMPTY = ["article_id"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        topic = agent_input.get("topic") or state.topic

        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"[lead_writer] Writing: {article['title']}")

        # Load prompts
        try:
            brand_voice = open("prompts/brand_voice.md").read()
            writer_prompt = open("prompts/lead_writer.md").read()
            system = f"{writer_prompt}\n\n---\n\n{brand_voice}"
        except FileNotFoundError:
            system = "You are a professional content writer for a Bangalore real estate platform. Write data-backed, conversational articles."

        # Get facts from knowledge base
        facts_text = ""
        try:
            from db.chroma_ops import search_facts
            keywords = json.loads(article.get("target_keywords", "{}") or "{}")
            primary_kw = keywords.get("primary", article["title"]) if isinstance(keywords, dict) else article["title"]
            facts = search_facts(f"{primary_kw} Bangalore", top_k=15)
            facts_text = "\n".join([f"- {f['text']}" for f in facts])
        except Exception:
            pass

        # Get cluster articles for linking
        cluster_articles = get_articles_by_cluster(self.cluster_id) if self.cluster_id else []
        other_articles = [a for a in cluster_articles if a["id"] != article_id]
        linking_context = "\n".join([f"- [{a['title']}](/{a['slug']})" for a in other_articles[:10]])

        # Get article spec from cluster plan
        cluster_plan = state.get(StateKeys.CLUSTER_PLAN, {})
        article_spec = None
        for a in cluster_plan.get("articles", []):
            if a.get("slug") == article["slug"]:
                article_spec = a
                break

        internal_links_spec = ""
        if article_spec and "internal_links" in article_spec:
            internal_links_spec = json.dumps(article_spec["internal_links"], indent=2)

        faqs = json.loads(article.get("faq_json", "[]") or "[]")
        faq_text = ""
        if faqs:
            faq_text = "\n\nFAQS TO INCLUDE:\n"
            for faq in faqs:
                faq_text += f"\nQ: {faq.get('question', '')}\nA: {faq.get('answer', '')}\n"

        outline = json.loads(article.get("outline", "[]") or "[]")
        keywords = json.loads(article.get("target_keywords", "{}") or "{}")

        prompt = f"""Write an article for Canvas Homes.

ARTICLE SPECIFICATION:
- Title: {article['title']}
- Slug: /{article['slug']}
- Type: {article['article_type']}
- Target Word Count: {article_spec.get('word_count_target', 2000) if article_spec else 2000}
- Primary Keyword: {keywords.get('primary', article['title']) if isinstance(keywords, dict) else article['title']}
- Secondary Keywords: {json.dumps(keywords.get('secondary', []) if isinstance(keywords, dict) else [])}

OUTLINE:
{json.dumps(outline, indent=2)}

FACTS FROM KNOWLEDGE BASE:
{facts_text or "No specific facts — use your Bangalore knowledge and note sources."}

INTERNAL LINKS:
{internal_links_spec or linking_context or "Link naturally to other articles."}
{faq_text}

Write the complete article. Start with H1 title. Follow brand voice exactly.
"""
        result = call_llm(
            prompt, system=system, model_role="writer",
            max_tokens=8000, temperature=0.4, use_cache=False,
        )
        self._track_llm(result)

        content = result["text"]
        word_count = len(content.split())

        update_article(article_id, content_md=content, word_count=word_count,
                       status="written", current_stage="lead_writer")
        add_article_history(article_id, "lead_writer",
                           f"Written {word_count} words", content[:200])

        # Store in ChromaDB
        try:
            from db.chroma_ops import store_article_embedding
            store_article_embedding(article_id, content, {"title": article["title"], "slug": article["slug"]})
        except Exception:
            pass

        print(f"  ✅ Written {word_count} words")
        return {"article_id": article_id, "word_count": word_count}