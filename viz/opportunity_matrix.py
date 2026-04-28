"""
viz/opportunity_matrix.py — builds scatter plot data for the dashboard.
"""

import math


def build_opportunity_matrix(trend_data, cluster_plan=None, faq_plan=None):
    """Build the 2-axis opportunity data for the scatter chart."""
    articles = []
    faqs = []

    # Get AEO scores from trend data
    aeo_scores = {}
    raw = trend_data.get("raw_data", {}) if trend_data else {}
    for score_item in raw.get("aeo_scores", []):
        q = score_item.get("query", "").lower()
        aeo_scores[q] = score_item.get("score", 50)

    # Build article dots
    if cluster_plan:
        for art in cluster_plan.get("articles", []):
            kw = art.get("target_keywords", {})
            primary = kw.get("primary", "") if isinstance(kw, dict) else ""
            aeo = aeo_scores.get(primary.lower(), 50)

            type_volume = {"hub": 80, "spoke": 55, "sub_spoke": 35, "faq": 45}
            vol = type_volume.get(art.get("type", "spoke"), 50)

            articles.append({
                "label": art.get("title", "Untitled"),
                "slug": art.get("slug", ""),
                "query": primary,
                "type": "article",
                "article_type": art.get("type", "spoke"),
                "x": vol + (hash(primary) % 20 - 10),  # jitter
                "y": min(100, max(0, aeo)),
                "size": aeo,
                "size_visual": max(8, math.sqrt(aeo) * 3),
            })

    # Build FAQ dots
    if faq_plan and isinstance(faq_plan, dict):
        faqs_by_article = faq_plan.get("faqs_by_article", {})
        for art_id, faq_list in faqs_by_article.items():
            for faq in (faq_list if isinstance(faq_list, list) else []):
                q = faq.get("question", "")
                kw = faq.get("target_keyword", q)
                aeo = aeo_scores.get(kw.lower(), 60)

                faqs.append({
                    "label": q[:60],
                    "slug": "",
                    "query": kw,
                    "type": "faq",
                    "article_type": "faq",
                    "intent": "informational",
                    "parent_slug": art_id,
                    "x": 30 + (hash(q) % 40),
                    "y": min(100, max(0, aeo + 10)),
                    "size": aeo,
                    "size_visual": max(6, math.sqrt(aeo) * 2.5),
                })

    return {
        "articles": articles,
        "faqs": faqs,
        "axis_meta": {
            "x_label": "Search Volume Score",
            "y_label": "Ease Score (100 = no competition)",
            "quadrant_guide": {
                "top_right": "🎯 GO NOW — high volume, low competition",
                "top_left": "🌱 Quick wins — lower volume but easy",
                "bottom_right": "🏔 Strategic — high volume, tough competition",
                "bottom_left": "❌ Skip — low volume, high competition",
            },
        },
    }