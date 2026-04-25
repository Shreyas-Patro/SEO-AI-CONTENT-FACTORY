"""
Keyword Mapper v3.

Fixes from round 2:
- Validator was too strict — required quick_win_keywords + strategic_keywords
  fields that the LLM sometimes packed under different names like
  'content_strategy_recommendations'. Relaxed: only keyword_groups is required.
- Retry now actually re-calls the LLM (uses _retry_suffix() in cache_namespace).
- On retry, prompt explicitly tells the LLM what was missing.
"""

import json
from llm import call_llm_json
from agents.base import AgentBase


class KeywordMapperAgent(AgentBase):
    NAME = "keyword_mapper"
    INPUT_REQUIRED = ["topic", "trend_data", "competitor_data"]
    # RELAXED: only keyword_groups + total_keywords are truly required.
    # quick_win_keywords/strategic_keywords are nice-to-have; we'll auto-fill.
    OUTPUT_REQUIRED = ["topic", "keyword_groups"]
    OUTPUT_NON_EMPTY = ["keyword_groups"]

    def validate_output(self, output):
        is_valid, problems = super().validate_output(output)

        groups = output.get("keyword_groups", [])
        if not isinstance(groups, list) or len(groups) < 3:
            problems.append(f"keyword_groups too few ({len(groups) if isinstance(groups,list) else 0}); expected ≥3")
            is_valid = False

        # Each group must have group_name + primary_keyword + suggested_article_type
        for i, g in enumerate(groups):
            if not isinstance(g, dict):
                problems.append(f"keyword_groups[{i}] is not a dict")
                is_valid = False
                continue
            for required in ("group_name", "primary_keyword", "suggested_article_type"):
                if not g.get(required):
                    problems.append(f"keyword_groups[{i}] missing/empty: {required}")
                    is_valid = False

        return is_valid, problems

    def _execute(self, validated_input):
        topic = validated_input["topic"]
        trend_data = validated_input["trend_data"]
        competitor_data = validated_input["competitor_data"]

        retry = self._retry_attempt
        retry_msg = ""
        if retry > 0 and self._retry_problems:
            retry_msg = (
                "\n\n⚠️ YOUR PREVIOUS RESPONSE WAS INVALID. Issues:\n"
                + "\n".join(f"  - {p}" for p in self._retry_problems)
                + "\n\nFix these in this response. Make sure 'keyword_groups' is a list of "
                "at least 6 dicts, each with 'group_name', 'primary_keyword', and "
                "'suggested_article_type' fields populated.\n"
            )

        print(f"\n[{self.NAME}] Mapping keywords for: {topic}{' (retry ' + str(retry) + ')' if retry else ''}")

        prompt_template = open("prompts/keyword_mapper.md").read()

        trend_analysis = trend_data.get("analysis", trend_data)
        raw = trend_data.get("raw_data", {})
        comp_analysis = competitor_data.get("analysis", competitor_data)

        prompt = f"""Create a keyword strategy for "{topic}" in Bangalore.

TOPIC: {topic}

TREND DATA:
{json.dumps(trend_analysis, indent=2)[:5000]}

RAW SEARCH QUERIES FOUND:
- PAA Questions: {json.dumps(raw.get("paa_questions", []), indent=2)[:2000]}
- Related Searches: {json.dumps(raw.get("related_searches", []), indent=2)[:2000]}
- Autocomplete: {json.dumps(raw.get("autocomplete", []), indent=2)[:1500]}

COMPETITOR ANALYSIS:
{json.dumps(comp_analysis, indent=2)[:2500]}

REQUIRED OUTPUT FIELDS (all must be present):
- "topic": "{topic}"
- "total_keywords": integer
- "keyword_groups": list of 6+ groups, each with group_name, primary_keyword, secondary_keywords[],
  long_tail_keywords[], faq_keywords[], suggested_article_type, difficulty, priority, estimated_volume
- "quick_win_keywords": list of 5+ low-difficulty keywords
- "strategic_keywords": list of 5+ high-value keywords

Map all discovered queries into a structured keyword plan FOR THE TOPIC: {topic}{retry_msg}
"""
        result = call_llm_json(
            prompt,
            system=prompt_template,
            model_role="bulk",
            max_tokens=8000,
            cache_namespace=f"{topic}:keyword_mapper{self._retry_suffix()}",   # retry busts cache
        )
        self._track_llm(result)

        analysis = result.get("parsed", {})
        analysis["topic"] = topic

        # Auto-fill missing convenience fields if model didn't include them
        if "total_keywords" not in analysis:
            total = sum(
                len(g.get("secondary_keywords", [])) + len(g.get("long_tail_keywords", [])) + len(g.get("faq_keywords", []))
                for g in analysis.get("keyword_groups", [])
            )
            analysis["total_keywords"] = total

        if "quick_win_keywords" not in analysis:
            # Pull low-difficulty keywords from groups
            qw = []
            for g in analysis.get("keyword_groups", []):
                if g.get("difficulty", "").lower() in ("low", "easy"):
                    qw.append(g.get("primary_keyword", ""))
                    qw.extend(g.get("secondary_keywords", [])[:2])
            analysis["quick_win_keywords"] = [k for k in qw if k][:15]

        if "strategic_keywords" not in analysis:
            sk = []
            for g in analysis.get("keyword_groups", []):
                if g.get("priority", "").lower() in ("high", "critical"):
                    sk.append(g.get("primary_keyword", ""))
            analysis["strategic_keywords"] = [k for k in sk if k][:10]

        return analysis

    def _output_summary(self, output):
        groups = output.get("keyword_groups", [])
        kw = output.get("total_keywords", 0)
        return f"{kw} keywords across {len(groups)} article groups"


def run_keyword_mapper(seed_topic, trend_data, competitor_data, cluster_id=None, pipeline_run_id=None):
    from db.artifacts import create_pipeline_run
    if pipeline_run_id is None:
        pipeline_run_id = create_pipeline_run(seed_topic, notes="standalone keyword_mapper run")

    agent = KeywordMapperAgent(pipeline_run_id, cluster_id=cluster_id)
    output = agent.run({
        "topic": seed_topic,
        "trend_data": trend_data,
        "competitor_data": competitor_data,
    })

    return {
        "topic": seed_topic,
        "keyword_map": output,
        "cost_usd": agent.cost_usd,
        "pipeline_run_id": pipeline_run_id,
    }
