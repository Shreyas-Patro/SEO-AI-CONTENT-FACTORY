"""
Competitor Spy Agent
Maps what competitors have published for a topic.
"""

import json
import time
from serpapi import GoogleSearch
from config_loader import get_serpapi_key, cfg
from db.sqlite_ops import cache_get, cache_set, start_agent_run, complete_agent_run, fail_agent_run
from llm import call_llm_json

SERPAPI_KEY = get_serpapi_key()
COMPETITORS = cfg.get("competitors", ["magicbricks.com", "nobroker.in", "housing.com", "99acres.com"])


def _search_competitor(competitor, topic):
    cache_key = f"comp:{competitor}:{topic}"
    cached = cache_get(cache_key)
    if cached:
        return cached

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
            {"title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")}
            for r in results.get("organic_results", [])
        ]
        cache_set(cache_key, data, ttl_days=7)
        time.sleep(1)
        return data
    except Exception as e:
        print(f"  Error searching {competitor}: {e}")
        return []


def run_competitor_spy(seed_topic, cluster_id=None):
    run_id = start_agent_run("competitor_spy", cluster_id=cluster_id,
                             input_summary=f"Topic: {seed_topic}")
    try:
        print(f"\n[Competitor Spy] Analyzing: {seed_topic}")

        all_results = {}
        for comp in COMPETITORS:
            print(f"  Searching {comp}...")
            results = _search_competitor(comp, seed_topic)
            all_results[comp] = results
            print(f"    Found {len(results)} results")

        # LLM analysis
        print("  Analyzing with LLM...")
        prompt_template = open("prompts/competitor_spy.md").read()
        prompt = f"""Analyze competitor coverage for "{seed_topic}" in Bangalore.

COMPETITOR SEARCH RESULTS:
{json.dumps(all_results, indent=2)}

Analyze each competitor's coverage depth and identify gaps we can exploit.
"""
        result = call_llm_json(prompt, system=prompt_template, model_role="bulk")
        analysis = result.get("parsed", {})

        output = {
            "topic": seed_topic,
            "raw_results": all_results,
            "analysis": analysis,
            "cost_usd": result.get("cost_usd", 0),
        }

        complete_agent_run(run_id,
                          output_summary=f"Analyzed {len(COMPETITORS)} competitors",
                          tokens_in=result.get("tokens_in", 0),
                          tokens_out=result.get("tokens_out", 0),
                          cost_usd=result.get("cost_usd", 0))

        print(f"  ✅ Competitor Spy complete.")
        return output

    except Exception as e:
        fail_agent_run(run_id, str(e))
        raise


if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "HSR Layout"
    result = run_competitor_spy(topic)
    print(json.dumps(result["analysis"], indent=2))