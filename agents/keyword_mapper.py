"""
agents/keyword_mapper.py — v3.2 (handles call_llm_json wrapper response)

Your call_llm_json returns:
    {
      "text": "<raw JSON string>",
      "parsed": <pre-parsed dict>,
      "parse_success": bool,
      "tokens_in": int, "tokens_out": int,
      "cost_usd": float, "model": str, "cached": bool
    }

This version:
- Reads the actual content from `parsed` (or parses `text` as fallback)
- Tracks the REAL cost/tokens from the wrapper
- Looks at all known keys trend_scout might use for PAA/related/autocomplete
- Logs what's in trend_data so we can see why payload is empty

CONTRACT:
    READS_STATE   = [TREND_DATA, COMPETITOR_DATA]
    WRITES_STATE  = [KEYWORD_MAP]
    OUTPUT        = {topic, keyword_groups[], summary}
"""

import json
from agents.base import AgentBase
from db.pipeline_state import StateKeys, PipelineState

from llm import call_llm_json


SYSTEM_PROMPT = """You are a senior SEO strategist analyzing keyword data for Bangalore real estate content.

Your job: take raw search data (PAA questions, related searches, autocomplete suggestions, competitor coverage) and organize it into 4-10 cohesive KEYWORD GROUPS that should each become a distinct piece of content.

A good keyword group:
- Has ONE primary keyword (the head term) and 3-7 supporting keywords (related long-tails, questions, variants)
- Targets a SINGLE search intent (informational, transactional, navigational, comparison)
- Has clear topical coherence
- Avoids overlap with other groups
- Has an opportunity_score reflecting (search interest) - (competitor strength)

CRITICAL: You MUST produce at least 5 keyword groups. Do not return an empty array.

Return STRICT JSON only:
{
  "topic": "<topic>",
  "keyword_groups": [
    {
      "group_name": "Short label (3-5 words)",
      "primary_keyword": "head term",
      "supporting_keywords": ["term1", "term2"],
      "intent": "informational|transactional|navigational|comparison",
      "estimated_volume": "high|medium|low",
      "competition": "high|medium|low",
      "opportunity_score": 0-100
    }
  ],
  "summary": "1-2 sentences"
}

NO markdown, NO commentary, JUST the JSON object."""


class KeywordMapperAgent(AgentBase):
    NAME = "keyword_mapper"
    READS_STATE = [StateKeys.TREND_DATA, StateKeys.COMPETITOR_DATA]
    WRITES_STATE = [StateKeys.KEYWORD_MAP]
    OUTPUT_REQUIRED = ["topic", "keyword_groups"]
    OUTPUT_NON_EMPTY = ["keyword_groups"]

    def _execute(self, state: PipelineState, agent_input: dict) -> dict:
        topic = agent_input.get("topic") or state.topic or ""
        trend_data = (
            agent_input.get("trend_data")
            or state.get(StateKeys.TREND_DATA, {})
            or {}
        )
        competitor_data = (
            agent_input.get("competitor_data")
            or state.get(StateKeys.COMPETITOR_DATA, {})
            or {}
        )

        print(f"[keyword_mapper] Mapping keywords for: {topic}")

        # ── DIAGNOSTIC: show what trend_data has so we can fix payload extraction ─
        if trend_data:
            print(f"  trend_data keys: {list(trend_data.keys())}")
        else:
            print(f"  ⚠️  trend_data is EMPTY")
        if competitor_data:
            print(f"  competitor_data keys: {list(competitor_data.keys())}")

        payload = self._build_payload(topic, trend_data, competitor_data)
        print(f"  Payload contains: "
              f"{len(payload['paa_questions'])} PAA, "
              f"{len(payload['related_searches'])} related, "
              f"{len(payload['autocomplete_suggestions'])} autocomplete, "
              f"{len(payload['uncovered_queries'])} uncovered")

        retry_note = ""
        if self._retry_attempt > 1 and self._validation_problems:
            retry_note = (
                f"\n\nIMPORTANT: previous attempt had problems: "
                f"{self._validation_problems}. Fix them."
            )

        user_prompt = (
            f"Topic: {topic}\n\n"
            f"Raw search data:\n{json.dumps(payload, indent=2, default=str)}"
            f"{retry_note}"
        )

        print(f"  Calling LLM (prompt: {len(user_prompt)} chars)")

        wrapper = call_llm_json(
            prompt=user_prompt,
            system=SYSTEM_PROMPT,
            model_role="bulk",
            max_tokens=4096,
            cache_namespace=f"{topic}:keyword_mapper{self._retry_suffix()}",
        )

        # Track real cost/tokens from the wrapper
        if isinstance(wrapper, dict):
            self.track_llm(
                tokens_in=wrapper.get("tokens_in", 0) or 0,
                tokens_out=wrapper.get("tokens_out", 0) or 0,
                cost=wrapper.get("cost_usd", 0) or 0,
                cache_hit=bool(wrapper.get("cached")),
            )
            print(f"  LLM: ${wrapper.get('cost_usd', 0):.4f} "
                  f"({wrapper.get('tokens_in', 0)}→{wrapper.get('tokens_out', 0)} tokens, "
                  f"model={wrapper.get('model', '?')})")

        # Extract the actual JSON content
        result = self._extract_content(wrapper)

        if not isinstance(result, dict):
            raise RuntimeError(
                f"Could not extract dict from LLM wrapper. "
                f"Got {type(result).__name__}: {str(result)[:300]}"
            )

        groups_raw = (
            result.get("keyword_groups")
            or result.get("groups")
            or result.get("keywords")
            or []
        )
        print(f"  Extracted {len(groups_raw)} keyword groups")

        if not groups_raw:
            print(f"  ❌ Result keys were: {list(result.keys())}")
            print(f"  ❌ Result preview: {json.dumps(result, default=str)[:500]}")

        clean_groups = []
        for g in groups_raw:
            if not isinstance(g, dict):
                continue
            clean_groups.append({
                "group_name":          g.get("group_name") or g.get("name") or "Untitled group",
                "primary_keyword":     g.get("primary_keyword") or g.get("primary") or "",
                "supporting_keywords": g.get("supporting_keywords") or g.get("supporting") or [],
                "intent":              g.get("intent", "informational"),
                "estimated_volume":    g.get("estimated_volume") or g.get("volume") or "medium",
                "competition":         g.get("competition", "medium"),
                "opportunity_score":   g.get("opportunity_score") or g.get("score") or 50,
            })

        print(f"  ✅ {len(clean_groups)} clean keyword groups produced")

        return {
            "topic": topic,
            "keyword_groups": clean_groups,
            "summary": result.get("summary", ""),
        }

    # ─── Helpers ───────────────────────────────────────────────────────────
    def _extract_content(self, wrapper):
        """Pull the actual JSON content out of call_llm_json's wrapper response."""
        if not isinstance(wrapper, dict):
            return wrapper

        # Path 1: pre-parsed dict (most common when parse_success=True)
        if wrapper.get("parsed") and isinstance(wrapper["parsed"], dict):
            return wrapper["parsed"]

        # Path 2: parse the `text` field ourselves
        text = wrapper.get("text") or ""
        if isinstance(text, str) and text.strip():
            stripped = text.strip()
            # Strip markdown code fences if present
            if stripped.startswith("```"):
                lines = stripped.split("\n")
                # drop first line ```json and last line ```
                if lines[-1].strip().startswith("```"):
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                stripped = "\n".join(lines).strip()
            try:
                return json.loads(stripped)
            except Exception as e:
                print(f"  Failed to parse text field: {e}")

        # Path 3: maybe the wrapper IS the content (unlikely but defensive)
        if "keyword_groups" in wrapper or "groups" in wrapper:
            return wrapper

        return None

    def _build_payload(self, topic, trend_data, competitor_data):
        """Extract data from trend_scout's nested output structure."""
        # Trend scout nests data under 'raw_data'
        raw = trend_data.get("raw_data", {}) or {}

        paa = (
            raw.get("paa_questions")
            or trend_data.get("paa_questions")
            or trend_data.get("people_also_ask")
            or []
        )
        related = (
            raw.get("related_searches")
            or trend_data.get("related_searches")
            or []
        )
        autocomplete = (
            raw.get("autocomplete")
            or trend_data.get("autocomplete_suggestions")
            or trend_data.get("autocomplete")
            or []
        )
        aeo_scores = (
            raw.get("aeo_scores")
            or trend_data.get("aeo_scores")
            or []
        )

        # Also pull from the LLM analysis
        analysis = trend_data.get("analysis", {}) or {}
        intent_clusters = analysis.get("intent_clusters", [])
        content_gaps = analysis.get("content_gaps", [])

        def to_text(items, key=None):
            seen, out = set(), []
            for it in items:
                if isinstance(it, dict):
                    text = (it.get(key or "question")
                            or it.get("query") or it.get("text")
                            or it.get("value") or it.get("title") or "")
                else:
                    text = str(it)
                text = text.strip() if isinstance(text, str) else ""
                if text and text.lower() not in seen:
                    seen.add(text.lower())
                    out.append(text)
            return out

        # Extract queries from intent clusters
        all_queries_from_analysis = []
        for cluster in intent_clusters:
            for q in cluster.get("queries", []):
                if q not in all_queries_from_analysis:
                    all_queries_from_analysis.append(q)

        # High AEO opportunities
        high_aeo = [s for s in aeo_scores if isinstance(s, dict) and s.get("score", 0) >= 60]

        return {
            "topic": topic,
            "paa_questions":            to_text(paa, key="question")[:25],
            "related_searches":         to_text(related)[:25],
            "autocomplete_suggestions": to_text(autocomplete)[:20],
            "queries_from_analysis":    all_queries_from_analysis[:20],
            "high_aeo_opportunities":   high_aeo[:15],
            "content_gaps":             content_gaps[:10],
            "competitor_coverage":      competitor_data.get("competitor_coverage", {}),
            "uncovered_queries":        competitor_data.get("uncovered_queries", [])[:15],
        }