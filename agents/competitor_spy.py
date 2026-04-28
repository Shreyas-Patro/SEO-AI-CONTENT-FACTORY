"""
agents/competitor_spy.py — Clean v3 implementation against AgentBase v4.2

CONTRACT:
    READS_STATE   = [TREND_DATA]
    WRITES_STATE  = [COMPETITOR_DATA]
    INPUT         = {"topic": str}  + auto-merged trend_data from state
    OUTPUT        = {
        "topic": str,
        "competitor_coverage": {domain: count},
        "raw_results": [...],
        "uncovered_queries": [...],
        "summary": str,
    }

The base class v4.2 handles validation, retries, persistence, state writes.
This file is pure business logic — no _retry_* attribute references.
"""

import os
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState

# Same imports as trend_scout.py
from serpapi import GoogleSearch


# Try to load API key from config (same way trend_scout does)
def _get_serpapi_key():
    try:
        from config_loader import cfg
        return cfg.get("serpapi_key") or cfg.get("serpapi", {}).get("api_key")
    except Exception:
        pass
    try:
        import yaml
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("serpapi_key") or cfg.get("serpapi", {}).get("api_key")
    except Exception:
        pass
    return os.environ.get("SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY")


COMPETITOR_DOMAINS = [
    "magicbricks.com",
    "nobroker.in",
    "housing.com",
    "99acres.com",
]


class CompetitorSpyAgent(AgentBase):
    NAME = "competitor_spy"
    READS_STATE = [StateKeys.TREND_DATA]
    WRITES_STATE = [StateKeys.COMPETITOR_DATA]
    OUTPUT_REQUIRED = ["topic", "competitor_coverage", "raw_results"]
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
        if not api_key:
            print("  ⚠️  No SerpAPI key found — falling back to trend_scout's coverage")
            existing = trend_data.get("competitor_coverage", {}) or {}
            return self._fallback_result(topic, existing)

        queries = self._build_queries(topic, trend_data)
        print(f"  Built {len(queries)} probe queries")

        raw_results = []
        domain_hits = {d: 0 for d in COMPETITOR_DOMAINS}

        for i, q in enumerate(queries, 1):
            print(f"  [{i}/{len(queries)}] {q}")
            try:
                organic = self._search(q, api_key)
                self.track_serp(cache_hit=False)
                raw_results.append({
                    "query": q,
                    "results": [
                        {"position": r.get("position"), "title": r.get("title", ""),
                         "link": r.get("link", "")}
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

        # Find queries where NO competitor ranks top-10 — gap opportunity
        uncovered = []
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

        summary = (
            f"{topic!r}: "
            + ", ".join(f"{d.split('.')[0]}={c}" for d, c in domain_hits.items())
            + f" | {len(uncovered)} uncovered queries"
        )
        print(f"  ✅ {summary}")

        return {
            "topic": topic,
            "competitor_coverage": domain_hits,
            "raw_results": raw_results,
            "uncovered_queries": uncovered,
            "summary": summary,
        }

    # ─── Helpers ───────────────────────────────────────────────────────────
    def _build_queries(self, topic: str, trend_data: dict) -> list:
        base = [
            f"{topic} property",
            f"{topic} apartments",
            f"{topic} 2bhk for sale",
            f"buy flat in {topic}",
            f"{topic} real estate review",
            f"living in {topic}",
        ]
        # Add a couple PAA questions if available
        paa = (
            trend_data.get("paa_questions")
            or trend_data.get("people_also_ask")
            or []
        )
        for q in paa[:2]:
            text = q.get("question") if isinstance(q, dict) else str(q)
            if text and text not in base:
                base.append(text)
        return base

    def _search(self, query: str, api_key: str) -> list:
        """Run a SerpAPI search exactly like trend_scout does."""
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google",
            "num": 10,
            "gl": "in",
            "hl": "en",
            "location": "Bangalore, Karnataka, India",
        }
        search = GoogleSearch(params)
        result = search.get_dict()
        return result.get("organic_results", []) or []

    def _fallback_result(self, topic: str, coverage: dict) -> dict:
        """Used when SerpAPI is unavailable — return minimal valid output."""
        normalized = {d: coverage.get(d, 0) for d in COMPETITOR_DOMAINS}
        return {
            "topic": topic,
            "competitor_coverage": normalized,
            "raw_results": [],
            "uncovered_queries": [],
            "summary": f"{topic!r}: fallback (no SERP) — used trend_scout's coverage data",
        }