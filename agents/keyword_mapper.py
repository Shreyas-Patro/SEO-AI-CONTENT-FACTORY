"""
Keyword Mapper Agent
Merges trend data + competitor data into a prioritized keyword strategy.
"""

import json
from db.sqlite_ops import start_agent_run, complete_agent_run, fail_agent_run
from llm import call_llm_json


def run_keyword_mapper(seed_topic, trend_data, competitor_data, cluster_id=None):
    run_id = start_agent_run("keyword_mapper", cluster_id=cluster_id,
                             input_summary=f"Topic: {seed_topic}")
    try:
        print(f"\n[Keyword Mapper] Mapping keywords for: {seed_topic}")

        prompt_template = open("prompts/keyword_mapper.md").read()
        prompt = f"""Create a keyword strategy for "{seed_topic}" in Bangalore.

TREND DATA:
{json.dumps(trend_data.get("analysis", {}), indent=2)}

RAW SEARCH QUERIES FOUND:
- PAA Questions: {json.dumps(trend_data.get("raw_data", {}).get("paa_questions", []), indent=2)}
- Related Searches: {json.dumps(trend_data.get("raw_data", {}).get("related_searches", []), indent=2)}
- Autocomplete: {json.dumps(trend_data.get("raw_data", {}).get("autocomplete", []), indent=2)}

COMPETITOR ANALYSIS:
{json.dumps(competitor_data.get("analysis", {}), indent=2)}

Map all discovered queries into a structured keyword plan.
"""
        result = call_llm_json(prompt, system=prompt_template, model_role="bulk", max_tokens=4096)
        analysis = result.get("parsed", {})

        output = {
            "topic": seed_topic,
            "keyword_map": analysis,
            "cost_usd": result.get("cost_usd", 0),
        }

        complete_agent_run(run_id,
                          output_summary=f"Mapped {analysis.get('total_keywords', 0)} keywords",
                          tokens_in=result.get("tokens_in", 0),
                          tokens_out=result.get("tokens_out", 0),
                          cost_usd=result.get("cost_usd", 0))

        print(f"  ✅ Keyword Mapper complete. {analysis.get('total_keywords', 0)} keywords mapped.")
        return output

    except Exception as e:
        fail_agent_run(run_id, str(e))
        raise


if __name__ == "__main__":
    # Test with mock data
    mock_trend = {"analysis": {"topic": "HSR Layout", "breakout_queries": ["HSR Layout metro"]}, "raw_data": {"paa_questions": ["Is HSR Layout good?"], "related_searches": ["HSR Layout rent"], "autocomplete": ["HSR Layout pin code"]}}
    mock_comp = {"analysis": {"coverage_gaps": [{"gap": "No legal guide"}]}}
    result = run_keyword_mapper("HSR Layout", mock_trend, mock_comp)
    print(json.dumps(result["keyword_map"], indent=2))