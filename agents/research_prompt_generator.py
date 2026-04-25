"""
Research Prompt Generator Agent (NEW)

Goal:
    Take all the planned articles (with outlines, FAQs, target keywords) and
    produce a structured, citation-demanding research prompt for Perplexity.

The output is a single MASTER PROMPT that, when pasted into Perplexity Pro,
returns research material covering every factual claim needed across the
entire content cluster. This is then fed to the Lead Writer.

Why a separate agent:
- Perplexity is expensive per query. Batching all questions into a single
  well-structured prompt saves 10x cost vs running per-article.
- The output is tightly structured (sections, citations, freshness rules)
  so the writer can pull exact facts deterministically.

Cost note:
- This is a big LLM call (uses Sonnet, 8K output tokens) because it
  digests outlines + FAQs across all 11 articles into one cohesive prompt.
- Expect ~$0.05-0.15 per cluster.

Output:
    {
      "master_research_prompt": "The full Perplexity prompt as one string",
      "research_questions": [{...structured per-section breakdown...}],
      "freshness_requirements": {...},
      "source_priority": [...],
      "estimated_perplexity_queries": 1,
      "estimated_perplexity_cost_usd": 0.20
    }
"""

import json
from llm import call_llm_json
from agents.base import AgentBase


SYSTEM_PROMPT = """You are a senior research strategist for Canvas Homes, a Bangalore real estate platform.

Your job: take a content cluster plan (articles, outlines, FAQs, keywords) and produce a SINGLE master research prompt that will be pasted into Perplexity Pro.

The master prompt MUST:
1. Demand citations from authoritative sources (RERA, BBMP, BDA, BMRDA, Karnataka Govt, Reuters, Economic Times, Mint, Hindu BusinessLine, MagicBricks data, NoBroker data, Knight Frank, JLL, Anarock, CBRE)
2. Demand freshness — facts must be from 2024-2026 unless historical context
3. Be structured into clear research sections matching article needs
4. Include specific numeric requirements (rent ranges, price/sqft, distance in km, dates of metro openings, etc.)
5. Demand contradictory data flagged separately (if MagicBricks says ₹X but NoBroker says ₹Y, surface both)
6. Demand source URLs in markdown format for every claim
7. Be ONE coherent prompt, not a list — Perplexity returns one long answer

Output format must be JSON with these fields:
- master_research_prompt: the full prompt string ready to paste into Perplexity
- research_questions: structured list of question groups (for our own audit)
- freshness_requirements: dict mapping topic→cutoff date
- source_priority: ordered list of preferred source domains
- estimated_perplexity_queries: integer (usually 1, sometimes 2 if cluster is huge)
- estimated_perplexity_cost_usd: float estimate

DO NOT include markdown fences. Return raw JSON only."""


class ResearchPromptGeneratorAgent(AgentBase):
    NAME = "research_prompt_generator"
    INPUT_REQUIRED = ["topic", "cluster_plan", "faqs_by_article"]
    OUTPUT_REQUIRED = [
        "master_research_prompt",
        "research_questions",
        "freshness_requirements",
        "source_priority"
    ]
    OUTPUT_NON_EMPTY = ["master_research_prompt", "research_questions"]

    def validate_output(self, output):
        is_valid, problems = super().validate_output(output)

        prompt = output.get("master_research_prompt", "")
        if isinstance(prompt, str) and len(prompt) < 500:
            problems.append(f"master_research_prompt too short ({len(prompt)} chars), expected 500+")
            is_valid = False

        # Must demand citations
        if isinstance(prompt, str) and "citation" not in prompt.lower() and "source" not in prompt.lower():
            problems.append("master_research_prompt does not demand citations/sources")
            is_valid = False

        return is_valid, problems

    def _execute(self, validated_input):
        topic = validated_input["topic"]
        cluster_plan = validated_input["cluster_plan"]
        faqs_by_article = validated_input["faqs_by_article"]

        print(f"\n[{self.NAME}] Building Perplexity research prompt for: {topic}")

        # Compress the cluster plan to keep prompt under control
        articles = cluster_plan.get("articles", [])
        compact_articles = []
        for a in articles:
            compact_articles.append({
                "title": a.get("title"),
                "type": a.get("type"),
                "primary_keyword": a.get("target_keywords", {}).get("primary"),
                "outline": a.get("outline", [])[:15],   # cap outline length
                "key_questions": a.get("notes", "")[:200],
            })

        # Flatten all FAQs into one list
        all_faqs = []
        for art_id, faqs in (faqs_by_article or {}).items():
            for f in faqs:
                all_faqs.append({
                    "article_id": art_id,
                    "question": f.get("question"),
                    "target_keyword": f.get("target_keyword"),
                })

        prompt = f"""Build a master Perplexity research prompt for the topic: "{topic}" (Bangalore real estate).

CONTENT CLUSTER ({len(articles)} articles):
{json.dumps(compact_articles, indent=2)[:6000]}

ALL FAQS THAT NEED ANSWERS ({len(all_faqs)} total):
{json.dumps(all_faqs, indent=2)[:4000]}

Now produce the master research prompt. It must:

1. Open with a clear role for Perplexity ("You are a real estate research analyst...")
2. State the location precisely: {topic}, Bangalore, Karnataka, India
3. List required research sections derived from the article outlines (Property Pricing, Rental Market, Connectivity, Schools, Hospitals, Legal/Regulatory, Investment Outlook, etc.)
4. For EACH section, list the specific questions to answer (drawing from the article outlines and FAQs above)
5. Demand citations: every numeric claim and every named source must include a markdown link [Source Name](URL)
6. Demand source diversity: mix of government (BBMP/BDA/RERA-K), industry (Knight Frank, JLL, Anarock, CBRE), real estate platforms (MagicBricks, NoBroker, 99acres, Housing.com), and news (Mint, Economic Times, Hindu BusinessLine, Reuters, Bloomberg, Moneycontrol)
7. Demand freshness: prefer sources from last 18 months; flag anything older as "[HISTORICAL]"
8. Demand contradictions are surfaced separately ("Conflicting data:" sections)
9. Demand a final "Confidence Notes" section flagging weak claims

Return JSON only — no fences, no preamble.
"""

        result = call_llm_json(
            prompt,
            system=SYSTEM_PROMPT,
            model_role="architect",     # uses Sonnet for higher quality
            max_tokens=8000,
            cache_namespace=f"{topic}:research_prompt_generator",
        )
        self._track_llm(result)

        output = result.get("parsed", {})

        # Defensive defaults
        output.setdefault("estimated_perplexity_queries", 1)
        output.setdefault("estimated_perplexity_cost_usd", 0.20)
        output.setdefault("freshness_requirements", {"default": "last 18 months"})
        output.setdefault("source_priority", [
            "rera.karnataka.gov.in",
            "bbmp.gov.in",
            "bda.karnataka.gov.in",
            "knightfrank.com",
            "jll.co.in",
            "anarock.com",
            "magicbricks.com",
            "nobroker.in",
            "livemint.com",
            "economictimes.indiatimes.com",
            "thehindubusinessline.com",
        ])

        return output

    def _output_summary(self, output):
        prompt_len = len(output.get("master_research_prompt", ""))
        q_count = len(output.get("research_questions", []))
        return f"{prompt_len} char prompt, {q_count} question groups"


def run_research_prompt_generator(topic, cluster_plan, faqs_by_article,
                                  cluster_id=None, pipeline_run_id=None):
    """Compatibility wrapper."""
    from db.artifacts import create_pipeline_run
    if pipeline_run_id is None:
        pipeline_run_id = create_pipeline_run(topic, notes="standalone research_prompt_generator run")

    agent = ResearchPromptGeneratorAgent(pipeline_run_id, cluster_id=cluster_id)
    output = agent.run({
        "topic": topic,
        "cluster_plan": cluster_plan,
        "faqs_by_article": faqs_by_article,
    })
    output["cost_usd"] = agent.cost_usd
    output["pipeline_run_id"] = pipeline_run_id
    return output


if __name__ == "__main__":
    print("Test: requires a real cluster_plan; run via dashboard pipeline.")