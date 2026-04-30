"""
agents/competitor_spy.py — v4 (SERP + LLM analysis)

Combines:
- v3's SERP-based competitor coverage tracking
- The old version's LLM analysis layer (so we get insights, not just hit counts)
"""
import json
import os

from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState
from llm import call_llm_json
from serpapi import GoogleSearch


def _get_serpapi_key():
    try:
        from config_loader import cfg
        return cfg.get("serpapi", {}).get("api_key") or cfg.get("serpapi_key")
    except Exception:
        pass
    return os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")


COMPETITOR_DOMAINS = ["magicbricks.com", "nobroker.in", "housing.com", "99acres.com"]


SYSTEM_PROMPT = """You are a competitive intelligence analyst for Canvas Homes (Bangalore real estate).

Given competitor SERP data and per-domain coverage counts, produce structured insights.

For each competitor classify:
- depth: "hub" (comprehensive guides), "spoke" (focused subtopics), "listing" (property listings), "blog" (blog posts), "thin" (low-quality / sparse)
- strengths and weaknesses

Then identify:
- coverage_gaps: topics they don't cover that we should target first
- our_advantages: angles where we can win (data freshness, local depth, AEO)

Return STRICT JSON only — no markdown, no commentary:
{
  "topic": "string",
  "competitor_summary": [
    {
      "competitor": "magicbricks.com",
      "result_count": 8,
      "depth": "hub|spoke|listing|blog|thin",
      "strengths": "string",
      "weaknesses": "string"
    }
  ],
  "coverage_gaps": [
    {
      "gap": "string",
      "priority": "high|medium|low",
      "suggested_article_type": "hub|spoke|sub_spoke|faq"
    }
  ],
  "our_advantages": ["string1", "string2"],
  "summary": "1-2 sentences"
}"""


class CompetitorSpyAgent(AgentBase):
    NAME = "competitor_spy"
    READS_STATE = [StateKeys.TREND_DATA]
    WRITES_STATE = [StateKeys.COMPETITOR_DATA]
    OUTPUT_REQUIRED = ["topic", "competitor_coverage", "raw_results", "analysis"]
    OUTPUT_NON_EMPTY = ["competitor_coverage"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        topic = agent_input.get("topic") or state.topic or ""
        trend_data = (
            agent_input.get("trend_data")
            or state.get(StateKeys.TREND_DATA, {})
            or {}
        )
        print(f"[competitor_spy] Analyzing: {topic}")

        api_key = _get_serpapi_key()
        domain_hits = {d: 0 for d in COMPETITOR_DOMAINS}
        raw_results = []
        uncovered = []

        if api_key:
            queries = self._build_queries(topic, trend_data)
            print(f"  Built {len(queries)} probe queries")

            for i, q in enumerate(queries, 1):
                print(f"  [{i}/{len(queries)}] {q}")
                try:
                    organic = self._search(q, api_key)
                    self.track_serp(cache_hit=False)
                    raw_results.append({
                        "query": q,
                        "results": [
                            {"position": r.get("position"),
                             "title": r.get("title", ""),
                             "link": r.get("link", ""),
                             "snippet": r.get("snippet", "")}
                            for r in organic[:10]
                        ],
                    })
                    for r in organic[:10]:
                        link = r.get("link", "") if isinstance(r, dict) else ""
                        for domain in COMPETITOR_DOMAINS:
                            if domain in link:
                                domain_hits[domain] += 1
                                break
                except Exception as e:
                    print(f"    SERP error: {e}")
                    raw_results.append({"query": q, "error": str(e)})

            for entry in raw_results:
                if "error" in entry:
                    continue
                results = entry.get("results") or []
                covered = any(
                    any(d in (r.get("link", "") if isinstance(r, dict) else "")
                        for d in COMPETITOR_DOMAINS)
                    for r in results
                )
                if not covered:
                    uncovered.append(entry["query"])
        else:
            print("  ⚠️  No SerpAPI key — using trend_scout's competitor data only")
            scout_coverage = trend_data.get("competitor_tracker", {}) or {}
            for d in COMPETITOR_DOMAINS:
                domain_hits[d] = scout_coverage.get(d, 0)

        # ── LLM analysis on top of the raw data ───────────────────────
        print("  Running LLM analysis...")
        compact_serp = []
        for r in raw_results[:8]:
            top3 = (r.get("results") or [])[:3]
            compact_serp.append({
                "query": r["query"],
                "top": [{"title": t["title"][:100], "link": t["link"]} for t in top3]
            })

        analysis_prompt = f"""Analyze competitor coverage for "{topic}" in Bangalore.

DOMAIN HIT COUNTS (out of {len(raw_results)} probe queries):
{json.dumps(domain_hits, indent=2)}

UNCOVERED QUERIES (no competitor in top 10):
{json.dumps(uncovered[:20], indent=2)}

SAMPLE SERP RESULTS:
{json.dumps(compact_serp, indent=2)[:5000]}

Provide structured analysis."""

        wrapper = call_llm_json(
            analysis_prompt,
            system=SYSTEM_PROMPT,
            model_role="bulk",
            max_tokens=3000,
            cache_namespace=f"{topic}:competitor_spy{self._retry_suffix()}",
        )
        self._track_llm(wrapper)
        analysis = wrapper.get("parsed", {}) if isinstance(wrapper, dict) else {}

        summary = (
            f"{topic!r}: "
            + ", ".join(f"{d.split('.')[0]}={c}" for d, c in domain_hits.items())
            + f" | {len(uncovered)} uncovered | {len(analysis.get('coverage_gaps', []))} gaps identified"
        )
        print(f"  ✅ {summary}")

        return {
            "topic": topic,
            "competitor_coverage": domain_hits,
            "raw_results": raw_results,
            "uncovered_queries": uncovered,
            "analysis": analysis,
            "summary": summary,
        }

    def _build_queries(self, topic, trend_data):
        base = [
            f"{topic} property",
            f"{topic} apartments",
            f"{topic} 2bhk for sale",
            f"buy flat in {topic}",
            f"{topic} real estate review",
            f"living in {topic}",
        ]
        raw = trend_data.get("raw_data", {}) or {}
        paa = raw.get("paa_questions") or trend_data.get("paa_questions") or []
        for q in paa[:2]:
            text = q.get("question") if isinstance(q, dict) else str(q)
            if text and text not in base:
                base.append(text)
        return base

    def _search(self, query, api_key):
        params = {
            "q": query, "api_key": api_key, "engine": "google",
            "num": 10, "gl": "in", "hl": "en",
            "location": "Bangalore, Karnataka, India",
        }
        return GoogleSearch(params).get_dict().get("organic_results", []) or []