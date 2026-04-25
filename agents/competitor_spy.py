"""
Competitor Spy v3 — proper retry cache busting (uses _retry_suffix from base).
"""

import json
import time
from serpapi import GoogleSearch
from config_loader import get_serpapi_key, cfg
from db.sqlite_ops import cache_get, cache_set
from llm import call_llm_json
from agents.base import AgentBase

SERPAPI_KEY = get_serpapi_key()
COMPETITORS = cfg.get("competitors", ["magicbricks.com", "nobroker.in", "housing.com", "99acres.com"])


def _search_competitor(competitor, topic):
    cache_key = f"comp:{competitor}:{topic}"
    cached = cache_get(cache_key)
    if cached:
        return cached, True

    try:
        params = {
            "engine": "google",
            "q": f"site:{competitor} {topic} Bangalore",
            "gl": "in",
            "hl": "en",
            "num": 10,
            "api_key": SERPAPI_KEY,
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        data = [
            {"title": r.get("title", ""),
             "link": r.get("link", ""),
             "snippet": r.get("snippet", "")}
            for r in results.get("organic_results", [])
        ]
        cache_set(cache_key, data, ttl_days=7)
        time.sleep(1)
        return data, False
    except Exception as e:
        print(f"  Error searching {competitor}: {e}")
        return [], False


class CompetitorSpyAgent(AgentBase):
    NAME = "competitor_spy"
    INPUT_REQUIRED = ["topic"]
    OUTPUT_REQUIRED = ["topic", "competitor_coverage", "coverage_gaps", "our_advantages", "raw_results"]
    OUTPUT_NON_EMPTY = ["competitor_coverage", "raw_results"]

    def validate_output(self, output):
        is_valid, problems = super().validate_output(output)

        raw = output.get("raw_results", {})
        for comp in COMPETITORS:
            if comp not in raw:
                problems.append(f"raw_results missing competitor: {comp}")
                is_valid = False

        coverage_competitors = {c.get("competitor") for c in output.get("competitor_coverage", [])}
        for comp in COMPETITORS:
            if comp in raw and len(raw[comp]) > 0 and comp not in coverage_competitors:
                problems.append(f"competitor_coverage missing analysis for: {comp}")
                is_valid = False

        return is_valid, problems

    def _build_analysis_prompt(self, topic, all_results, retry_problems=None):
        strict_clause = ""
        if retry_problems:
            strict_clause = (
                "\n\n⚠️ YOUR PREVIOUS RESPONSE WAS INCOMPLETE. Issues:\n"
                + "\n".join(f"  - {p}" for p in retry_problems)
                + "\n\nYou MUST analyze ALL "
                f"{len(COMPETITORS)} competitors below, even if some have few results. "
                "Each competitor MUST appear in competitor_coverage.\n"
            )
        return f"""Analyze competitor coverage for "{topic}" in Bangalore.

COMPETITOR SEARCH RESULTS (all {len(COMPETITORS)} competitors):
{json.dumps(all_results, indent=2)}

Analyze each competitor's coverage depth and identify gaps we can exploit.{strict_clause}
"""

    def _execute(self, validated_input):
        topic = validated_input["topic"]
        retry = self._retry_attempt
        print(f"\n[{self.NAME}] Analyzing: {topic}{' (retry ' + str(retry) + ')' if retry else ''}")

        # SERP only on first attempt; reuse results on retry
        if retry == 0:
            all_results = {}
            for comp in COMPETITORS:
                print(f"  Searching {comp}...")
                results, was_cached = _search_competitor(comp, topic)
                all_results[comp] = results
                if not was_cached:
                    self._track_serp(1)
                print(f"    Found {len(results)} results ({'cached' if was_cached else 'fresh'})")
            self._cached_serp_results = all_results
        else:
            all_results = getattr(self, "_cached_serp_results", {})
            print(f"  Reusing SERP results from attempt 1")

        print("  Analyzing with LLM...")
        prompt_template = open("prompts/competitor_spy.md").read()
        prompt = self._build_analysis_prompt(topic, all_results, retry_problems=self._retry_problems)

        result = call_llm_json(
            prompt,
            system=prompt_template,
            model_role="bulk",
            max_tokens=8000,
            cache_namespace=f"{topic}:competitor_spy{self._retry_suffix()}",
        )
        self._track_llm(result)

        analysis = result.get("parsed", {})

        return {
            "topic": topic,
            "raw_results": all_results,
            "competitor_coverage": analysis.get("competitor_coverage", []),
            "coverage_gaps": analysis.get("coverage_gaps", []),
            "our_advantages": analysis.get("our_advantages", []),
        }

    def _output_summary(self, output):
        cov = len(output.get("competitor_coverage", []))
        gaps = len(output.get("coverage_gaps", []))
        return f"{cov} competitors analyzed, {gaps} gaps found"


def run_competitor_spy(seed_topic, cluster_id=None, pipeline_run_id=None):
    from db.artifacts import create_pipeline_run
    if pipeline_run_id is None:
        pipeline_run_id = create_pipeline_run(seed_topic, notes="standalone competitor_spy run")

    agent = CompetitorSpyAgent(pipeline_run_id, cluster_id=cluster_id)
    output = agent.run({"topic": seed_topic})

    return {
        "topic": seed_topic,
        "raw_results": output["raw_results"],
        "analysis": {
            "competitor_coverage": output["competitor_coverage"],
            "coverage_gaps": output["coverage_gaps"],
            "our_advantages": output["our_advantages"],
        },
        "cost_usd": agent.cost_usd,
        "pipeline_run_id": pipeline_run_id,
    }


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "HSR Layout"
    result = run_competitor_spy(topic)
    print(json.dumps(result["analysis"], indent=2))