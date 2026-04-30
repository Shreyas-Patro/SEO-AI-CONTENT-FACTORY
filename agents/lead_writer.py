"""
agents/lead_writer.py — v6 (research-first, batched fact lookup)

Workflow:
1. Pull all the relevant facts from ChromaDB for this article's keywords + outline
2. Identify which outline sections still lack supporting facts
3. Batch ONE Claude call to fill those gaps (not N calls per gap)
4. Write the article with strict citation discipline
5. Auto-rewrite if word_count < 80% of target
"""
import json
import re
from typing import List, Dict
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState
from db.sqlite_ops import (
    get_article, update_article, add_article_history, get_articles_by_cluster,
)
from llm import call_llm, call_llm_json


GAP_FILL_SYSTEM = """You are a research assistant for Canvas Homes (Bangalore real estate).

You are given a list of outline sections that need supporting facts. For EACH section,
provide 2-4 specific data points (numbers, dates, sources, named entities) that the
writer can cite.

Rules:
- All data must be plausible for Bangalore real estate as of 2024-2026
- Always include a source attribution (e.g., "BBMP 2026", "RERA Karnataka", "Knight Frank Q1 2026")
- Prefer specific neighborhoods, sectors, prices, percentages
- If you cannot supply a fact for a section confidently, say "needs_external_research" for that section

Return STRICT JSON only:
{
  "facts_by_section": {
    "Section heading 1": [
      {"fact": "...", "source": "..."},
      {"fact": "...", "source": "..."}
    ]
  }
}"""


class LeadWriterAgent(AgentBase):
    NAME = "lead_writer"
    READS_STATE = [StateKeys.CLUSTER_PLAN, StateKeys.TREND_DATA]
    OUTPUT_REQUIRED = ["article_id", "word_count"]
    MAX_VALIDATION_RETRIES = 1   # writer auto-extends, doesn't need outer retries

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        topic = agent_input.get("topic") or state.topic
        rewrite_feedback = agent_input.get("rewrite_feedback")  # set by rewriter

        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"[lead_writer] Writing: {article['title']}")
        if rewrite_feedback:
            print(f"  ↻ rewrite mode — feedback len={len(rewrite_feedback)}")

        # ── Article spec from cluster plan ────────────────────────────
        cluster_plan = state.get(StateKeys.CLUSTER_PLAN, {}) or {}
        article_spec = self._find_spec(cluster_plan, article)
        word_target = article_spec.get("word_count_target", 2500) if article_spec else 2500

        keywords = json.loads(article.get("target_keywords", "{}") or "{}")
        primary_kw = keywords.get("primary", article["title"]) if isinstance(keywords, dict) else article["title"]
        outline = json.loads(article.get("outline", "[]") or "[]")

        # ── Step 1: Pull all relevant facts from ChromaDB ─────────────
        kb_facts = self._pull_kb_facts(article, primary_kw, topic, outline)
        print(f"  KB facts retrieved: {len(kb_facts)}")

        # ── Step 2: Identify outline sections lacking facts ───────────
        sections_needing_facts = self._identify_fact_gaps(outline, kb_facts)
        print(f"  Sections needing facts: {len(sections_needing_facts)}")

        # ── Step 3: ONE batched Claude call to fill gaps ──────────────
        gap_facts = {}
        if sections_needing_facts:
            gap_facts = self._batched_gap_fill(
                article["title"], primary_kw, sections_needing_facts, topic
            )

        # ── Step 4: Build the writing prompt ──────────────────────────
        try:
            brand_voice = open("prompts/brand_voice.md").read()
            writer_prompt = open("prompts/lead_writer.md").read()
            system = f"{writer_prompt}\n\n---\n\n{brand_voice}"
        except FileNotFoundError:
            system = self._fallback_system()

        serp_context = self._extract_serp_context(state, article)
        cluster_articles = get_articles_by_cluster(self.cluster_id) if self.cluster_id else []
        link_context = self._build_link_context(article_id, cluster_articles, article_spec)
        faqs = json.loads(article.get("faq_json", "[]") or "[]")
        faq_block = self._faq_block(faqs)
        facts_block = self._format_facts_block(kb_facts, gap_facts)

        prompt = f"""Write a complete article for Canvas Homes.

ARTICLE SPEC:
- Title: {article['title']}
- Slug: /{article['slug']}
- Type: {article['article_type']}
- MINIMUM Word Count: {word_target} (write MORE — never less)
- Primary Keyword: {primary_kw}
- Secondary Keywords: {json.dumps(keywords.get('secondary', []) if isinstance(keywords, dict) else [])}

OUTLINE:
{json.dumps(outline, indent=2)}

{facts_block}

{serp_context}

{link_context}
{faq_block}

REWRITE FEEDBACK (if present, address these specifically):
{rewrite_feedback or "—"}

REQUIREMENTS:
1. AT LEAST {word_target} words. Count yourself. If under, expand.
2. Every section has specific numbers, dates, prices, or named sources.
3. Use "you/your". Short paragraphs (max 3 sentences).
4. ≥3 internal links in [text](/slug) format.
5. Start with H1. ## for H2. ### for H3.
6. End with the FAQ section if FAQs are provided.
7. Bangalore-native: "locality" not "neighborhood", "auto" not "rickshaw".
8. Cite every statistic inline: [Source: Name, Year](URL) or [Source: Name, Year].

Write the complete article now."""

        result = call_llm(
            prompt, system=system, model_role="writer",
            max_tokens=12000, temperature=0.4, use_cache=False,
        )
        self._track_llm(result)

        content = result["text"]
        word_count = len(content.split())

        # ── Step 5: Auto-extend if too short ──────────────────────────
        if word_count < int(word_target * 0.8):
            print(f"  Auto-extending: {word_count} < {int(word_target * 0.8)}")
            extension = call_llm(
                f"This article is only {word_count} words but should be ≥{word_target}. "
                f"Add 2-3 substantial sections with new facts and data — do NOT repeat. "
                f"Return ONLY the new sections in markdown:\n\n{content[:6000]}",
                system=system, model_role="writer",
                max_tokens=6000, temperature=0.5, use_cache=False,
            )
            self._track_llm(extension)
            content = content.rstrip() + "\n\n" + extension["text"]
            word_count = len(content.split())

        # ── Step 6: Persist ───────────────────────────────────────────
        update_article(
            article_id, content_md=content, word_count=word_count,
            status="written", current_stage="lead_writer",
        )
        add_article_history(
            article_id, "lead_writer",
            f"Written {word_count} words"
            + (" (rewrite)" if rewrite_feedback else ""),
            content[:500],
        )

        try:
            from db.chroma_ops import store_article_embedding
            store_article_embedding(article_id, content,
                                    {"title": article["title"], "slug": article["slug"]})
        except Exception:
            pass

        print(f"  Done: {word_count} words (target: {word_target})")
        return {"article_id": article_id, "word_count": word_count}

    # ─── Helpers ──────────────────────────────────────────────────────
    def _find_spec(self, cluster_plan: dict, article: dict) -> dict:
        for a in cluster_plan.get("articles", []):
            if a.get("slug") == article["slug"] or a.get("db_id") == article["id"]:
                return a
        return {}

    def _pull_kb_facts(self, article, primary_kw, topic, outline):
        facts = []
        try:
            from db.chroma_ops import search_facts
            queries = [primary_kw, f"{primary_kw} Bangalore", topic]
            for h in outline[:8]:
                heading = h.replace("H2: ", "").replace("H3: ", "").strip()
                if heading:
                    queries.append(f"{heading} {topic}")
            seen = set()
            for q in queries:
                results = search_facts(q, top_k=5)
                for f in results:
                    if f["text"] not in seen:
                        seen.add(f["text"])
                        facts.append(f)
        except Exception as e:
            print(f"  Warning: ChromaDB search failed: {e}")
        return facts

    def _identify_fact_gaps(self, outline, kb_facts):
        if not outline:
            return []
        kb_text = " ".join([f.get("text", "")[:200] for f in kb_facts]).lower()
        gaps = []
        for h in outline:
            heading = h.replace("H2: ", "").replace("H3: ", "").strip()
            if not heading or heading.startswith("#"):
                continue
            keywords = [w.lower() for w in heading.split() if len(w) > 4]
            hits = sum(1 for w in keywords if w in kb_text)
            if hits < max(1, len(keywords) // 3):
                gaps.append(heading)
        return gaps[:8]

    def _batched_gap_fill(self, title, primary_kw, sections, topic):
        prompt = f"""Article title: {title}
Primary keyword: {primary_kw}
Topic context: {topic}

Sections needing supporting facts:
{json.dumps(sections, indent=2)}

For each section, provide 2-4 specific data points the writer can cite."""
        result = call_llm_json(
            prompt, system=GAP_FILL_SYSTEM, model_role="bulk",
            max_tokens=3000,
            cache_namespace=f"{topic}:gapfill:{title[:30]}",
        )
        self._track_llm(result)
        parsed = result.get("parsed", {}) or {}
        return parsed.get("facts_by_section", {})

    def _format_facts_block(self, kb_facts, gap_facts):
        out = []
        if kb_facts:
            out.append("FACTS FROM OUR KNOWLEDGE BASE (use with citations — verified):")
            for f in kb_facts[:25]:
                source = f.get("metadata", {}).get("source", "Canvas Homes Research")
                out.append(f"- {f['text']} [Source: {source}]")
            out.append("")
        if gap_facts:
            out.append("GAP-FILL FACTS (mark approximate when used; cite source given):")
            for section, facts in gap_facts.items():
                out.append(f"  Section: {section}")
                for f in (facts or []):
                    if isinstance(f, dict):
                        out.append(f"    • {f.get('fact','')} [Source: {f.get('source','')}]")
            out.append("")
        if not out:
            return "NOTE: No research facts available — use approximations and label them as such."
        return "\n".join(out)

    def _extract_serp_context(self, state, article):
        out = []
        trend_data = state.get(StateKeys.TREND_DATA, {}) or {}
        raw = trend_data.get("raw_data", {}) or {}
        paa = raw.get("paa_questions", [])
        title_words = set(article["title"].lower().split())
        relevant = []
        for q in paa[:30]:
            q_text = q if isinstance(q, str) else q.get("question", "")
            q_words = set(q_text.lower().split())
            if len(title_words & q_words) >= 2:
                relevant.append(q_text)
        if relevant:
            out.append("REAL QUESTIONS PEOPLE SEARCH (answer these in the article):")
            for q in relevant[:8]:
                out.append(f"- {q}")
        analysis = trend_data.get("analysis", {}) or {}
        comp = analysis.get("competitor_insights", [])
        if comp:
            out.append("\nCOMPETITOR WEAKNESSES TO EXPLOIT:")
            for c in comp[:3]:
                out.append(f"- {c.get('competitor','?')}: {c.get('weakness','')}")
        return "\n".join(out) if out else ""

    def _build_link_context(self, article_id, cluster_articles, article_spec):
        others = [a for a in cluster_articles if a["id"] != article_id]
        if article_spec and article_spec.get("internal_links"):
            out = "REQUIRED INTERNAL LINKS (use these exact slugs and anchors):\n"
            for link in article_spec["internal_links"]:
                out += f'- /{link["target_slug"]} as: "{link["anchor_text"]}"\n'
            return out
        return "ARTICLES IN CLUSTER (link to ≥3):\n" + "\n".join(
            [f"- [{a['title']}](/{a['slug']})" for a in others[:10]]
        )

    def _faq_block(self, faqs):
        if not faqs:
            return ""
        out = "\n\nFAQ SECTION (include at the bottom under '## Frequently Asked Questions'):\n"
        for f in faqs:
            out += f"\nQ: {f.get('question','')}\nA: {f.get('answer','')}\n"
        return out

    def _fallback_system(self):
        return (
            "You write data-backed conversational articles for Canvas Homes "
            "(Bangalore real estate). Use 'you/your', short paragraphs, H2 every "
            "300-400 words, question-format H2s, end with takeaway. Start with H1. "
            "Cite every stat inline."
        )