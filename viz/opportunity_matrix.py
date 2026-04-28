"""
Opportunity Matrix — converts Trend Scout + Competitor Spy + Keyword Mapper
output into a 2-axis scatter plot dataset.

AXES (mathematically valid, both bounded [0, 100]):
- X: search_volume_score
    Components:
      a) Google Trends recent_interest        (0-100, direct from SerpAPI Trends)
      b) PAA presence count                   (count of PAA questions / 5, capped at 100)
      c) Related search density               (count / 4, capped at 100)
    Combined: weighted average. If trends unavailable, falls back to b+c only.

- Y: ease_score (inverse of competitor density)
    For each query:
      ease = 100 - (competitor_count_in_top_10 * 12.5)
    competitor_count is from Trend Scout's competitor_presence array per query.
    Bounded [0, 100]. Higher = easier to win.

DOT SIZE: aeo_opportunity_score (already 0-100 from Trend Scout)
    Scaled with sqrt for perceptually-correct area mapping.

COLOR / TAB:
    - "article": represents a planned article (from cluster_plan)
    - "faq": represents an FAQ slot (from faq_plan)
    - "both": shown in the combined tab
"""

import math
from typing import Optional


def _clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _query_to_volume_score(query: str, trend_data: dict, serp_results: list) -> float:
    """
    Compute a search volume proxy 0-100 for a single query.
    Uses Trend Scout's raw data — no new API calls.
    """
    # Component A: trends recent_interest (if available)
    trends = trend_data.get("raw_data", {}).get("trends", {})
    trends_score = trends.get("recent_interest", 0) if trends.get("trend_available") else 0

    # Component B: PAA hits — does this query show up in PAA?
    paa_qs = trend_data.get("raw_data", {}).get("paa_questions", [])
    q_lower = query.lower()
    paa_hits = sum(1 for p in paa_qs if q_lower[:30] in p.lower() or p.lower()[:30] in q_lower)
    paa_score = _clamp(paa_hits * 20)  # 5 hits = 100

    # Component C: related searches density
    rel_qs = trend_data.get("raw_data", {}).get("related_searches", [])
    rel_hits = sum(1 for r in rel_qs if q_lower[:30] in r.lower())
    rel_score = _clamp(rel_hits * 25)  # 4 hits = 100

    # Component D: total_results from SERP (logarithmic — high competition = high interest)
    serp_match = next(
        (s for s in serp_results if s.get("query", "").lower() == q_lower),
        None,
    )
    if serp_match and serp_match.get("total_results"):
        # Bangalore real estate queries: 100K results = 50, 10M = 100
        total = serp_match["total_results"]
        log_score = _clamp(math.log10(max(total, 1)) * 12.5)
    else:
        log_score = 0

    # Weighted combine
    if trends.get("trend_available"):
        # Trends data is most reliable
        return _clamp(0.5 * trends_score + 0.2 * paa_score + 0.15 * rel_score + 0.15 * log_score)
    else:
        # Fallback: no trends data
        return _clamp(0.4 * paa_score + 0.3 * rel_score + 0.3 * log_score)


def _query_to_ease_score(query: str, aeo_scores: list) -> float:
    """
    Y-axis: ease of ranking. Inverse of competitor density.
    """
    # Find the AEO record for this query (Trend Scout already computed it)
    record = next(
        (a for a in aeo_scores if a.get("query", "").lower() == query.lower()),
        None,
    )
    if not record:
        return 50.0  # neutral default

    # Trend Scout's aeo score already factors in: no FS, no AIO, no competitor presence
    # We can use it directly as ease, but blend with explicit competitor count
    aeo = record.get("score", 50)
    has_comp = record.get("competitor_present", False)
    has_fs = record.get("has_featured_snippet", False)

    # If competitor has the featured snippet: very hard to displace
    if has_comp and has_fs:
        ease = aeo * 0.3
    elif has_comp:
        ease = aeo * 0.6
    elif has_fs:
        ease = aeo * 0.7  # Some site has it but not a competitor
    else:
        ease = aeo  # Open field

    return _clamp(ease)


def build_opportunity_matrix(
    trend_data: dict,
    cluster_plan: Optional[dict] = None,
    faq_plan: Optional[dict] = None,
) -> dict:
    """
    Build the dataset that powers the opportunity scatter plot.

    Returns:
    {
      "articles": [
        {label, query, x, y, size, type, slug, primary_keyword, ...}
      ],
      "faqs": [
        {label, query, x, y, size, type, parent_slug, ...}
      ],
      "axis_meta": {
         "x_label": "Search Volume Score (0-100)",
         "y_label": "Ease Score (100 - competitor density)",
         "size_label": "AEO Opportunity Score",
         "size_scale": "sqrt"
      }
    }
    """
    if not trend_data:
        return {"articles": [], "faqs": [], "axis_meta": {}}

    raw = trend_data.get("raw_data", {})
    aeo_scores = raw.get("aeo_scores", [])
    serp_results = raw.get("serp_results_summary", [])

    # ─── Articles ─────────────────────────────────────────────────────────
    articles_dots = []
    if cluster_plan:
        for art in cluster_plan.get("articles", []):
            primary = art.get("target_keywords", {}).get("primary", "")
            if not primary:
                continue
            x = _query_to_volume_score(primary, trend_data, serp_results)
            y = _query_to_ease_score(primary, aeo_scores)

            # AEO score for this article's primary keyword
            aeo_record = next(
                (a for a in aeo_scores if a.get("query", "").lower() == primary.lower()),
                None,
            )
            aeo = aeo_record.get("score", 50) if aeo_record else 50

            articles_dots.append({
                "label": art.get("title", "")[:60],
                "query": primary,
                "x": round(x, 1),
                "y": round(y, 1),
                "size": aeo,
                "size_visual": math.sqrt(max(aeo, 1)) * 5,  # perceptually correct area
                "type": "article",
                "article_type": art.get("type"),
                "slug": art.get("slug"),
                "secondary_kw_count": len(art.get("target_keywords", {}).get("secondary", [])),
            })

    # ─── FAQs ─────────────────────────────────────────────────────────────
    faqs_dots = []
    if faq_plan:
        allocation = faq_plan.get("allocation_by_article", {})
        for slug, faqs in allocation.items():
            for faq in faqs:
                q = faq.get("question", "")
                if not q:
                    continue
                # FAQs may not have appeared as exact SERP queries — use partial match
                x = _query_to_volume_score(q, trend_data, serp_results)
                y = _query_to_ease_score(q, aeo_scores)

                aeo_record = next(
                    (a for a in aeo_scores if a.get("query", "").lower() == q.lower()),
                    None,
                )
                aeo = aeo_record.get("score", 50) if aeo_record else (
                    {"high": 75, "medium": 55, "low": 35}.get(faq.get("aeo_value", "medium"), 50)
                )

                faqs_dots.append({
                    "label": q[:60],
                    "query": q,
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "size": aeo,
                    "size_visual": math.sqrt(max(aeo, 1)) * 5,
                    "type": "faq",
                    "intent": faq.get("intent"),
                    "parent_slug": slug,
                    "aeo_value": faq.get("aeo_value"),
                })

    return {
        "articles": articles_dots,
        "faqs": faqs_dots,
        "axis_meta": {
            "x_label": "Search Volume Score (0-100)",
            "y_label": "Ease Score (0-100, higher = easier to rank)",
            "size_label": "AEO Opportunity Score (0-100)",
            "size_scale": "sqrt (visual area ≈ score)",
            "quadrant_guide": {
                "top_right": "🎯 GO NOW — high volume, easy to rank",
                "top_left":  "🌱 Quick wins — easy but low volume; cheap content",
                "bottom_right": "🏔  Strategic — high value but tough; needs pillar content",
                "bottom_left": "❌ Skip — low value, high competition",
            },
        },
    }