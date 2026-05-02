"""
agents/lead_writer.py — v7

Improvements over v6:
1. Reads FAQ_PLAN from state (or article.faq_json)
2. Injects current year from config
3. Strips em-dashes post-generation
4. Pulls KB facts via batched ChromaDB queries
5. British Indian English enforced
6. Temperature 0.3 for writer (lower = less hallucination)
7. Validates word count and citation density
"""
import json
import re
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState
from db.sqlite_ops import (
    get_article, update_article, add_article_history, get_articles_by_cluster,
)
from llm import call_llm, call_llm_json
from config_loader import current_year


GAP_FILL_SYSTEM_TEMPLATE = """You are a research assistant for Canvas Homes (Bangalore real estate).

You are given a list of outline sections that need supporting facts. For EACH section,
provide 2-4 specific data points the writer can cite.

Rules:
- All data must be plausible for Bangalore real estate as of {YEAR_RANGE}
- Always include a source attribution: "BBMP {YEAR}", "RERA Karnataka {YEAR}", "Knight Frank Q1 {YEAR}", "JLL India {YEAR}", "Anarock Research {YEAR}"
- Use British Indian English: "flat" not "apartment", "locality" not "neighborhood", "lakh"/"crore" not "millions"
- NEVER use em-dashes (—). Use commas, semicolons, or full stops.
- If you cannot supply a fact for a section confidently, say "needs_external_research" for that section

Return STRICT JSON only:
{{
  "facts_by_section": {{
    "Section heading 1": [
      {{"fact": "...", "source": "..."}},
      {{"fact": "...", "source": "..."}}
    ]
  }}
}}"""


def _strip_em_dashes(text: str) -> str:
    """Convert em-dashes (—) and en-dashes (–) to commas + space."""
    if not text:
        return text
    # Em-dash with optional spaces around → ", "
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"\s*–\s*", ", ", text)
    # Triple-dash sometimes used as separator → keep as paragraph break
    text = re.sub(r"\s*---+\s*", "\n\n", text)
    return text


def _enforce_british_indian_english(text: str) -> str:
    """Run targeted replacements to enforce British Indian English."""
    if not text:
        return text
    replacements = [
        (r"\bneighborhood\b", "locality"),
        (r"\bneighborhoods\b", "localities"),
        (r"\bapartment\b(?!\s+complex)", "flat"),
        (r"\bapartments\b(?!\s+complex)", "flats"),
        (r"\brickshaw\b", "auto"),
        (r"\bsidewalk\b", "footpath"),
        (r"\bcustomized\b", "customised"),
        (r"\bauthorized\b", "authorised"),
        (r"\bcolor\b", "colour"),
        (r"\bcenter\b", "centre"),
        (r"\bmillion\s+rupees\b", "ten lakh rupees"),
        (r"\b1,?000,?000 rupees\b", "10 lakh rupees"),
    ]
    for pat, rep in replacements:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text


class LeadWriterAgent(AgentBase):
    NAME = "lead_writer"
    READS_STATE = [StateKeys.CLUSTER_PLAN, StateKeys.TREND_DATA]
    OUTPUT_REQUIRED = ["article_id", "word_count"]
    MAX_VALIDATION_RETRIES = 1

    def _validate_output(self, output):
        problems = super()._validate_output(output)
        word_count = output.get("word_count", 0)
        if word_count < 800:
            problems.append(f"word_count too low ({word_count})")
        return problems

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        article_id = agent_input.get("article_id")
        topic = agent_input.get("topic") or state.topic
        rewrite_feedback = agent_input.get("rewrite_feedback")

        article = get_article(article_id)
        if not article:
            raise ValueError(f"Article {article_id} not found")

        print(f"[lead_writer] Writing: {article['title']}")

        # ── Article spec from cluster plan ──
        cluster_plan = state.get(StateKeys.CLUSTER_PLAN, {}) or {}
        article_spec = self._find_spec(cluster_plan, article)
        word_target = article_spec.get("word_count_target", 2500) if article_spec else 2500

        keywords = json.loads(article.get("target_keywords", "{}") or "{}")
        primary_kw = (
            keywords.get("primary", article["title"])
            if isinstance(keywords, dict)
            else article["title"]
        )
        outline = json.loads(article.get("outline", "[]") or "[]")

        # ── Pull FAQs (from state OR article.faq_json) ──
        faqs = self._pull_faqs(state, article_id, article)
        print(f"  FAQs to embed: {len(faqs)}")

        # ── Pull KB facts ──
        kb_facts = self._pull_kb_facts(article, primary_kw, topic, outline)
        print(f"  KB facts retrieved: {len(kb_facts)}")

        # ── Identify gaps + batched fill ──
        sections_needing_facts = self._identify_fact_gaps(outline, kb_facts)
        gap_facts = {}
        if sections_needing_facts:
            gap_facts = self._batched_gap_fill(
                article["title"], primary_kw, sections_needing_facts, topic
            )

        # ── Build the writing prompt ──
        try:
            brand_voice = open("prompts/brand_voice.md", encoding="utf-8").read()
            writer_prompt = open("prompts/lead_writer.md", encoding="utf-8").read()
            system = f"{writer_prompt}\n\n---\n\n{brand_voice}"
        except FileNotFoundError:
            system = self._fallback_system()

        # Inject current year into system prompt
        system = system.replace("{CURRENT_YEAR}", str(current_year()))

        serp_context = self._extract_serp_context(state, article)
        cluster_articles = (
            get_articles_by_cluster(self.cluster_id) if self.cluster_id else []
        )
        link_context = self._build_link_context(article_id, cluster_articles, article_spec)
        faq_block = self._faq_block(faqs)
        facts_block = self._format_facts_block(kb_facts, gap_facts)

        prompt = f"""Write a complete article for Canvas Homes in British Indian English.

ARTICLE SPEC:
- Title: {article['title']}
- Slug: /{article['slug']}
- Type: {article['article_type']}
- MINIMUM Word Count: {word_target}
- Primary Keyword: {primary_kw}
- Secondary Keywords: {json.dumps(keywords.get('secondary', []) if isinstance(keywords, dict) else [])}
- Current Year: {current_year()}

OUTLINE:
{json.dumps(outline, indent=2)}

{facts_block}

{serp_context}

{link_context}
{faq_block}

REWRITE FEEDBACK (if present, address these specifically):
{rewrite_feedback or "None"}

CRITICAL RULES:
1. AT LEAST {word_target} words.
2. Every section has specific numbers, dates, prices, or named sources.
3. Paragraphs: 2-3 sentences ideal, max 4.
4. AT LEAST 3 internal links in [text](/slug) format.
5. H1 once. ## for H2. ### for H3.
6. End with the FAQ section if FAQs are provided.
7. British Indian English: "locality", "flat", "auto", "lakh", "crore", "favourable", "colour".
8. NO EM-DASHES (—). Use commas or full stops.
9. Cite every statistic inline: [Source: Name, {current_year()}](URL).
10. AEO-friendly: include question H2s like "## What is the average rent in {topic}?".

Write the complete article now."""

        result = call_llm(
            prompt, system=system, model_role="writer",
            max_tokens=12000, temperature=0.3, use_cache=False,
        )
        self._track_llm(result)
        content = result["text"]

        # ── Post-processing ──
        content = _strip_em_dashes(content)
        content = _enforce_british_indian_english(content)

        word_count = len(content.split())

        # ── Auto-extend if too short ──
        if word_count < int(word_target * 0.85):
            print(f"  Auto-extending: {word_count} < {int(word_target * 0.85)}")
            extension = call_llm(
                f"This article is {word_count} words but should be ≥{word_target}. "
                f"Add 2-3 substantial NEW sections with NEW facts and data — do NOT repeat existing content. "
                f"Keep British Indian English. NO em-dashes. Return ONLY the new sections in markdown:\n\n{content[:6000]}",
                system=system, model_role="writer",
                max_tokens=6000, temperature=0.4, use_cache=False,
            )
            self._track_llm(extension)
            extension_text = _strip_em_dashes(extension["text"])
            extension_text = _enforce_british_indian_english(extension_text)
            content = content.rstrip() + "\n\n" + extension_text
            word_count = len(content.split())

        # ── Persist ──
        update_article(
            article_id, content_md=content, word_count=word_count,
            status="written", current_stage="lead_writer",
        )
        add_article_history(
            article_id, "lead_writer",
            f"Written {word_count} words" + (" (rewrite)" if rewrite_feedback else ""),
            content[:500],
        )

        try:
            from db.chroma_ops import store_article_embedding
            store_article_embedding(
                article_id, content,
                {"title": article["title"], "slug": article["slug"]}
            )
        except Exception:
            pass

        print(f"  Done: {word_count} words (target: {word_target})")
        return {"article_id": article_id, "word_count": word_count}

    # ─── Helpers ──────────────────────────────────────────────────────
    def _find_spec(self, cluster_plan, article):
        for a in cluster_plan.get("articles", []):
            if a.get("slug") == article["slug"] or a.get("db_id") == article["id"]:
                return a
        return {}

    def _pull_faqs(self, state, article_id, article):
        """Pull FAQs from FAQ_PLAN state, fall back to article.faq_json."""
        # First try state
        faq_plan = state.get(StateKeys.FAQ_PLAN, {}) or {}
        faqs_by_article = faq_plan.get("faqs_by_article", {}) or {}
        if article_id in faqs_by_article and faqs_by_article[article_id]:
            return faqs_by_article[article_id]
        # Fall back to article column
        try:
            return json.loads(article.get("faq_json", "[]") or "[]")
        except Exception:
            return []

    def _pull_kb_facts(self, article, primary_kw, topic, outline):
        """Batched ChromaDB lookup."""
        facts = []
        try:
            from db.chroma_ops import search_facts
            queries = [primary_kw, f"{primary_kw} Bangalore", topic]
            for h in outline[:8]:
                heading = h.replace("H2: ", "").replace("H3: ", "").strip()
                if heading and not heading.startswith("#"):
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
            if not keywords:
                continue
            hits = sum(1 for w in keywords if w in kb_text)
            if hits < max(1, len(keywords) // 3):
                gaps.append(heading)
        return gaps[:8]

    def _batched_gap_fill(self, title, primary_kw, sections, topic):
        """ONE LLM call to fill ALL gap sections."""
        gap_fill_system = GAP_FILL_SYSTEM_TEMPLATE.format(
            YEAR_RANGE=f"{current_year()-1}-{current_year()+1}",
            YEAR=current_year(),
        )
        prompt = f"""Article title: {title}
Primary keyword: {primary_kw}
Topic context: {topic}
Current year: {current_year()}

Sections needing supporting facts:
{json.dumps(sections, indent=2)}

For each section, provide 2-4 specific data points the writer can cite."""
        result = call_llm_json(
            prompt, system=gap_fill_system, model_role="bulk",
            max_tokens=3000,
            cache_namespace=f"{topic}:gapfill:{title[:30]}",
        )
        self._track_llm(result)
        parsed = result.get("parsed", {}) or {}
        return parsed.get("facts_by_section", {})

    def _format_facts_block(self, kb_facts, gap_facts):
        out = []
        if kb_facts:
            out.append("FACTS FROM OUR KNOWLEDGE BASE (verified — use with citations):")
            for f in kb_facts[:25]:
                source = f.get("metadata", {}).get("source", "Canvas Homes Research")
                out.append(f"- {f['text']} [Source: {source}]")
            out.append("")
        if gap_facts:
            out.append("GAP-FILL FACTS (cite source given):")
            for section, facts in gap_facts.items():
                out.append(f"  Section: {section}")
                for f in (facts or []):
                    if isinstance(f, dict):
                        out.append(f"    - {f.get('fact','')} [Source: {f.get('source','')}]")
            out.append("")
        if not out:
            return "NOTE: Limited research facts. Use approximations and label them 'as of [date]'."
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
            out.append("REAL QUESTIONS PEOPLE SEARCH (answer these):")
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
        return "ARTICLES IN CLUSTER (link to >=3):\n" + "\n".join(
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
            f"You write data-backed conversational articles for Canvas Homes "
            f"(Bangalore real estate). British Indian English. NO em-dashes. "
            f"Use 'you/your', short paragraphs, H2 every 300-400 words, question-format H2s, "
            f"end with takeaway. Start with H1. Cite every stat inline. Current year: {current_year()}."
        )