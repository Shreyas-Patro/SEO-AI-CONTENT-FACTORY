"""
agents/research_prompt_generator.py — v6

Improvements:
1. Reads BOTH cluster_plan and faq_plan
2. Comprehensive authoritative source list
3. Structured by article (not bulk)
4. Outputs Perplexity-ready format with explicit recency requirements
"""
import json
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from agents.base import AgentBase
from config_loader import current_year


AUTHORITATIVE_SOURCES = [
    # Government / Regulatory
    "rera.karnataka.gov.in", "bbmp.gov.in", "bda.karnataka.gov.in",
    "kar.nic.in", "incometax.gov.in", "rbi.org.in", "sebi.gov.in",
    "nhb.org.in", "moneycontrol.com",

    # Real Estate Research
    "knightfrank.co.in", "knightfrank.com", "jll.co.in", "jll.com",
    "anarock.com", "cbre.com", "cushmanwakefield.com", "colliers.com",
    "savills.in", "vestian.com", "propequity.in",

    # News / Industry
    "livemint.com", "economictimes.indiatimes.com", "business-standard.com",
    "thehindubusinessline.com", "moneylife.in", "constructionworld.in",
    "realestate-investments.com", "realtyplusmag.com",

    # Marketplaces (use cautiously — listing data, not editorial)
    "magicbricks.com", "nobroker.in", "housing.com", "99acres.com",
    "commonfloor.com", "makaan.com", "squareyards.com",

    # Local
    "deccanherald.com", "thehindu.com/news/cities/bangalore",
    "bangaloremirror.indiatimes.com",

    # Banking / Finance
    "sbi.co.in", "hdfcbank.com", "icicibank.com", "lichousing.com",
    "axisbank.com", "kotak.com", "bajajhousingfinance.in",
]


SYSTEM_PROMPT = f"""You are a senior research strategist for Canvas Homes (Bangalore real estate platform).

Produce a SINGLE master research prompt for Perplexity (or any deep research tool) that, when answered, will provide every factual claim needed across the entire content cluster — including the FAQs.

The master prompt MUST:
1. State the topic clearly with current year context ({current_year()}).
2. Demand citations from authoritative sources only — list them explicitly.
3. Demand freshness: data from the last 18 months unless inherently historical.
4. Structure the research into sections matching the article cluster.
5. Demand source URLs in markdown format.
6. Demand SPECIFIC numbers — exact prices, percentages, dates, named entities.
7. Demand both city-level and locality-specific data where relevant.
8. Include questions for FAQs explicitly.
9. Specify British Indian English ("flat" not "apartment", "lakh"/"crore", "BBMP", "RERA").
10. Be ONE coherent prompt — readable in <60 seconds, executable in one Perplexity query.

The output research will populate our knowledge graph and feed our Lead Writer.

Return STRICT JSON:
{{
  "master_research_prompt": "<2000-4000 word prompt>",
  "research_questions": [
    {{"section": "Property Prices", "questions": ["What is the average price per sqft for 2BHK in HSR Layout in Q1 {current_year()}?", "..."]}}
  ],
  "freshness_requirements": {{"default": "last 18 months", "exceptions": "historical claims may use any era"}},
  "source_priority": ["rera.karnataka.gov.in", "..."],
  "estimated_perplexity_queries": 1,
  "estimated_perplexity_cost_usd": 0.20
}}

NO markdown fences. Return raw JSON only."""


class ResearchPromptGeneratorAgent(AgentBase):
    NAME = "research_prompt_generator"
    READS_STATE = [StateKeys.CLUSTER_PLAN, StateKeys.FAQ_PLAN]
    WRITES_STATE = [StateKeys.RESEARCH_PROMPT]
    OUTPUT_REQUIRED = ["master_research_prompt", "research_questions"]
    OUTPUT_NON_EMPTY = ["master_research_prompt", "research_questions"]

    def _validate_output(self, output):
        problems = super()._validate_output(output)
        prompt = output.get("master_research_prompt", "") if isinstance(output, dict) else ""
        if isinstance(prompt, str):
            if len(prompt) < 800:
                problems.append(f"master_research_prompt too short ({len(prompt)} chars)")
            if "—" in prompt:
                problems.append("em-dashes detected in research prompt")
        return problems

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        topic = agent_input.get("topic") or state.topic or ""
        cluster_plan = (
            agent_input.get("cluster_plan")
            or state.get(StateKeys.CLUSTER_PLAN, {})
            or {}
        )
        faq_plan = (
            agent_input.get("faq_plan")
            or state.get(StateKeys.FAQ_PLAN, {})
            or {}
        )
        faqs_by_article = faq_plan.get("faqs_by_article", {}) or {}

        print(f"[{self.NAME}] Building research prompt for: {topic}")

        articles = cluster_plan.get("articles", [])
        # Build comprehensive article descriptors with their FAQs
        compact_articles = []
        for a in articles:
            kw = a.get("target_keywords") or {}
            article_faqs = faqs_by_article.get(a.get("db_id"), [])
            compact_articles.append({
                "title": a.get("title"),
                "type": a.get("type"),
                "primary_keyword": kw.get("primary") if isinstance(kw, dict) else "",
                "outline": (a.get("outline") or [])[:10],
                "faqs": [f.get("question", "") for f in article_faqs],
            })

        prompt = f"""Build a master Perplexity research prompt for: "{topic}" (Bangalore real estate, current year {current_year()}).

CONTENT CLUSTER ({len(articles)} articles, with FAQs embedded):
{json.dumps(compact_articles, indent=2)[:8000]}

AUTHORITATIVE SOURCES TO PRIORITISE:
{json.dumps(AUTHORITATIVE_SOURCES[:30])}

The output prompt should produce a research document that, when ingested into our knowledge base, will allow our writers to draft every article in the cluster — every claim cited, every FAQ answered, every number current as of {current_year()}.

Build the master research prompt as JSON.
"""

        result = call_llm_json(
            prompt, system=SYSTEM_PROMPT, model_role="architect",
            max_tokens=8000,
            cache_namespace=f"{topic}:research_prompt{self._retry_suffix()}",
        )
        self._track_llm(result)

        output = result.get("parsed", {}) or {}
        output.setdefault("estimated_perplexity_queries", 1)
        output.setdefault("estimated_perplexity_cost_usd", 0.30)
        output.setdefault("freshness_requirements", {"default": "last 18 months"})
        output.setdefault("source_priority", AUTHORITATIVE_SOURCES[:15])

        # Sanity post-process: strip em-dashes from prompt
        if "—" in output.get("master_research_prompt", ""):
            output["master_research_prompt"] = output["master_research_prompt"].replace("—", ", ")

        return output