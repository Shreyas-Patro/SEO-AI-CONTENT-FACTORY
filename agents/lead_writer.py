"""
agents/lead_writer.py — v5.1 (uses research data + SERP insights, writes longer)
"""

import json
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState
from db.sqlite_ops import get_article, update_article, add_article_history, get_articles_by_cluster
from llm import call_llm


class LeadWriterAgent(AgentBase):
    NAME = "lead_writer"
    READS_STATE = [StateKeys.CLUSTER_PLAN, StateKeys.TREND_DATA]
    OUTPUT_REQUIRED = ["article_id", "word_count"]

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
            system = (
                "You are a professional content writer for Canvas Homes, a Bangalore real estate platform. "
                "Write data-backed, conversational articles with specific numbers, dates, and citations. "
                "Use 'you/your'. Short paragraphs (2-3 sentences). H2 every 200-300 words. "
                "Include question-format H2s. End with actionable takeaway. Start with H1 title."
            )

        # ── Get facts from knowledge base (ChromaDB) ──
        facts_text = ""
        try:
            from db.chroma_ops import search_facts
            keywords = json.loads(article.get("target_keywords", "{}") or "{}")
            primary_kw = keywords.get("primary", article["title"]) if isinstance(keywords, dict) else article["title"]

            all_facts = []
            for query in [primary_kw, f"{primary_kw} Bangalore", topic, f"{topic} property prices"]:
                facts = search_facts(query, top_k=8)
                for f in facts:
                    if f["text"] not in [x["text"] for x in all_facts]:
                        all_facts.append(f)

            if all_facts:
                facts_text = "FACTS FROM OUR RESEARCH (use these with citations — they are verified):\n"
                for f in all_facts[:20]:
                    source = f.get("metadata", {}).get("source", "Canvas Homes Research")
                    facts_text += f"- {f['text']} [Source: {source}]\n"
                facts_text += f"\nTotal verified facts available: {len(all_facts)}\n"
        except Exception as e:
            print(f"  Warning: ChromaDB search failed: {e}")

        # ── Get SERP insights from trend_scout ──
        serp_context = ""
        trend_data = state.get(StateKeys.TREND_DATA, {})
        if trend_data:
            raw = trend_data.get("raw_data", {})
            paa = raw.get("paa_questions", [])
            analysis = trend_data.get("analysis", {})

            relevant_paa = []
            title_words = set(article["title"].lower().split())
            for q in paa[:30]:
                q_text = q if isinstance(q, str) else q.get("question", "")
                q_words = set(q_text.lower().split())
                if len(title_words & q_words) >= 2:
                    relevant_paa.append(q_text)

            if relevant_paa:
                serp_context += "REAL QUESTIONS PEOPLE ASK (from Google — answer these in the article):\n"
                for q in relevant_paa[:8]:
                    serp_context += f"- {q}\n"

            comp_insights = analysis.get("competitor_insights", [])
            if comp_insights:
                serp_context += "\nCOMPETITOR WEAKNESSES TO EXPLOIT:\n"
                for ci in comp_insights[:3]:
                    serp_context += f"- {ci.get('competitor','?')}: {ci.get('weakness','')}\n"

        # ── Get cluster articles for linking ──
        cluster_articles = get_articles_by_cluster(self.cluster_id) if self.cluster_id else []
        other_articles = [a for a in cluster_articles if a["id"] != article_id]
        linking_context = "\n".join([f"- [{a['title']}](/{a['slug']})" for a in other_articles[:10]])

        # ── Get article spec from cluster plan ──
        cluster_plan = state.get(StateKeys.CLUSTER_PLAN, {})
        article_spec = None
        for a in cluster_plan.get("articles", []):
            if a.get("slug") == article["slug"] or a.get("db_id") == article_id:
                article_spec = a
                break

        internal_links_spec = ""
        if article_spec and "internal_links" in article_spec:
            internal_links_spec = "REQUIRED INTERNAL LINKS (use these exact slugs and anchor texts):\n"
            for link in article_spec["internal_links"]:
                internal_links_spec += f'- Link to /{link["target_slug"]} with anchor text: "{link["anchor_text"]}"\n'

        # ── Get FAQs ──
        faqs = json.loads(article.get("faq_json", "[]") or "[]")
        faq_text = ""
        if faqs:
            faq_text = "\n\nFAQ SECTION (include at the bottom with ## Frequently Asked Questions):\n"
            for faq in faqs:
                faq_text += f"\nQ: {faq.get('question', '')}\nA: {faq.get('answer', '')}\n"

        outline = json.loads(article.get("outline", "[]") or "[]")
        keywords = json.loads(article.get("target_keywords", "{}") or "{}")
        word_target = article_spec.get("word_count_target", 2500) if article_spec else 2500

        prompt = f"""Write a complete article for Canvas Homes.

ARTICLE SPECIFICATION:
- Title: {article['title']}
- Slug: /{article['slug']}
- Type: {article['article_type']}
- MINIMUM Word Count: {word_target} words (this is a MINIMUM — write MORE, not less)
- Primary Keyword: {keywords.get('primary', article['title']) if isinstance(keywords, dict) else article['title']}
- Secondary Keywords: {json.dumps(keywords.get('secondary', []) if isinstance(keywords, dict) else [])}

OUTLINE TO FOLLOW:
{json.dumps(outline, indent=2)}

{facts_text or "NOTE: No research facts available yet. Use your knowledge of Bangalore real estate, but clearly mark any statistics as approximate."}

{serp_context}

{internal_links_spec or f"ARTICLES IN CLUSTER (link to at least 3):\\n{linking_context}"}
{faq_text}

CRITICAL REQUIREMENTS:
1. MINIMUM {word_target} words. Count your output. If under {word_target}, add more depth.
2. Every section must have specific numbers, prices, dates, or named sources.
3. Use "you/your" throughout. Short paragraphs (2-3 sentences max).
4. Include at least 3 internal links using [text](/slug) format.
5. Start with H1 title. Use ## for H2, ### for H3.
6. Include the FAQ section at the bottom if FAQs are provided.
7. Bangalore-native language: "locality" not "neighborhood", "auto" not "rickshaw".

Write the complete article now. Make it LONG and DETAILED."""

        result = call_llm(
            prompt, system=system, model_role="writer",
            max_tokens=12000,
            temperature=0.4, use_cache=False,
        )
        self._track_llm(result)

        content = result["text"]
        word_count = len(content.split())

        update_article(article_id, content_md=content, word_count=word_count,
                       status="written", current_stage="lead_writer")
        add_article_history(article_id, "lead_writer",
                           f"Written {word_count} words", content[:500])

        try:
            from db.chroma_ops import store_article_embedding
            store_article_embedding(article_id, content, {"title": article["title"], "slug": article["slug"]})
        except Exception:
            pass

        print(f"  Done: {word_count} words (target: {word_target})")
        return {"article_id": article_id, "word_count": word_count}