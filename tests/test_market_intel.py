"""
Test: Market intelligence agents — Trend Scout, Competitor Spy, Keyword Mapper.
After all agents pass, builds and displays the exact input packet that
Content Architect and FAQ Architect will receive.

WARNING: This test makes real SerpAPI calls. Costs ~15 SerpAPI searches + ~$0.02 LLM.
"""
import sys, os, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# PRESENTATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _box(title, width=70):
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)

def _section(title, width=70):
    print(f"\n  {'─' * (width - 4)}")
    print(f"  {title}")
    print(f"  {'─' * (width - 4)}")

def _bullet(label, value, indent=1):
    print(f"{'    ' * indent}• {label}: {value}")

def _numbered(items, indent=4, limit=None):
    pad = " " * indent
    for i, item in enumerate(items[:limit] if limit else items, 1):
        print(f"{pad}{i:>2}. {item}")
    if limit and len(items) > limit:
        print(f"{pad}    ... and {len(items) - limit} more")


def print_trend_scout_output(data):
    """Full presentation of Trend Scout results."""
    _box("📡  TREND SCOUT — OUTPUT REPORT")

    analysis = data.get("analysis", {})
    raw      = data.get("raw_data", {})
    clf      = data.get("classification", {})

    _section("TOPIC OVERVIEW")
    _bullet("Topic",          data.get("topic", "—"))
    _bullet("Type",           clf.get("primary_category", "—"))
    _bullet("All categories", ", ".join(clf.get("categories", [])))
    _bullet("Localities",     ", ".join(clf.get("detected_localities", [])) or "—")

    _section("TREND SIGNAL  ← from Google Trends via SerpAPI")
    direction = analysis.get("trend_direction", "unknown")
    arrow = {"rising_fast": "🚀", "rising": "📈", "stable": "➡️",
             "declining": "📉", "declining_fast": "💀"}.get(direction, "❓")
    print(f"    {arrow}  Direction : {direction.upper()}")
    print(f"    📝  Summary   : {analysis.get('trend_summary', '—')}")

    trends = raw.get("trends", {})
    if trends.get("trend_available"):
        avg    = trends.get("average_interest", 0)
        recent = trends.get("recent_interest", 0)
        print(f"\n    What these numbers mean:")
        print(f"    • {avg} and {recent} are Google Trends scores (0–100 scale),")
        print(f"      NOT raw search volumes. 100 = peak popularity in the window.")
        print(f"    • Recent {recent} vs avg {avg} = trend is pointing downward.")
        print(f"    • A score of {recent}/100 is still strong — not unpopular.")
        _bullet("Seasonal pattern",
                "Yes — publish content BEFORE peaks" if trends.get("is_seasonal") else "No")

    _section("RAW DATA COLLECTED  (all from live Google SerpAPI calls)")
    _bullet("PAA questions",    f"{len(raw.get('paa_questions', []))} — real questions people type into Google")
    _bullet("Related searches", f"{len(raw.get('related_searches', []))} — keywords Google suggests after this topic")
    _bullet("Autocomplete",     f"{len(raw.get('autocomplete', []))} — high-volume queries (Google only autocompletes frequent ones)")
    _bullet("SERP calls used",  f"{data.get('serp_calls_used', 0)}/15")
    _bullet("High AEO opps",    f"{len([s for s in raw.get('aeo_scores', []) if s.get('score', 0) >= 70])} queries with no strong AI answer — we can own these")

    _section("TOP PEOPLE-ALSO-ASK QUESTIONS  (first 10 — these are your article titles)")
    _numbered(raw.get("paa_questions", []), indent=4, limit=10)

    _section("INTENT CLUSTERS")
    for cluster in analysis.get("intent_clusters", []):
        intent = cluster.get("intent", "?").upper()
        vol    = cluster.get("estimated_volume", "?")
        aeo    = cluster.get("aeo_opportunity", "?")
        qs     = cluster.get("queries", [])
        print(f"\n    [{intent}]  volume={vol}  aeo_opportunity={aeo}")
        for q in qs[:4]:
            print(f"        – {q}")
        if len(qs) > 4:
            print(f"        ... +{len(qs)-4} more")

    _section("TOP AEO TARGETS  (queries where we can become the AI-cited answer)")
    aeo_targets = analysis.get("aeo_targets", [])
    if aeo_targets:
        for t in aeo_targets[:5]:
            print(f"\n    🎯  Query   : {t.get('query', '—')}")
            print(f"        Quality : {t.get('current_answer_quality', '—')}")
            print(f"        Strategy: {t.get('our_strategy', '—')}")
            print(f"        Type    : {t.get('content_type', '—')}")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    _section("CONTENT GAPS")
    gaps = analysis.get("content_gaps", [])
    if gaps:
        for gap in gaps[:5]:
            pri  = gap.get("priority", "?").upper()
            icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(pri, "⚪")
            print(f"\n    {icon} [{pri}]  {gap.get('gap', '—')}")
            print(f"       Opportunity : {gap.get('opportunity', '—')}")
            print(f"       Content type: {gap.get('suggested_content_type', '—')}")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    _section("TOP 5 PRIORITY QUERIES  (start here — highest ROI content to write first)")
    top5 = analysis.get("top_5_priority_queries", [])
    if top5:
        for i, q in enumerate(top5[:5], 1):
            print(f"\n    {i}. {q.get('query', '—')}")
            print(f"       Why   : {q.get('reason', '—')}")
            print(f"       Action: {q.get('suggested_action', '—')}")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    _section("COMPETITOR COVERAGE  (appearances across all 15 SERP results)")
    for comp, count in sorted(data.get("competitor_tracker", {}).items(),
                               key=lambda x: x[1], reverse=True):
        bar = "█" * min(count, 20)
        print(f"    {comp:<25} {bar} {count}  ← appeared in {count}/15 searches")

    related = analysis.get("related_topics_to_explore", [])
    if related:
        _section("RELATED TOPICS TO EXPLORE NEXT  (feed these back into Trend Scout)")
        _numbered(related, indent=4)

    _section("RUN COST")
    _bullet("LLM cost", f"${data.get('cost_usd', 0):.4f}")
    print()


def print_competitor_spy_output(data):
    """Full presentation of Competitor Spy results."""
    _box("🕵️  COMPETITOR SPY — OUTPUT REPORT")

    analysis = data.get("analysis", {})
    raw      = data.get("raw_results", {})

    _section("WHAT THIS AGENT DID")
    print("    Ran 4 site-scoped Google searches (one per competitor):")
    print('    e.g. "site:magicbricks.com Electronic City Bangalore"')
    print("    Collected all articles each competitor has published for this topic.")
    print("    Asked Claude to classify each article and identify gaps.")

    _section("RAW RESULT COUNTS  (articles found per competitor)")
    for comp, results in raw.items():
        bar = "█" * min(len(results), 20)
        print(f"    {comp:<30} {bar} {len(results)} articles")

    _section("COMPETITOR COVERAGE BREAKDOWN")
    for comp_block in analysis.get("competitor_coverage", []):
        comp     = comp_block.get("competitor", "?")
        articles = comp_block.get("articles", [])
        found    = comp_block.get("articles_found", len(articles))
        print(f"\n    🏢  {comp}  ({found} articles found)")
        print(f"    {'─'*62}")
        for art in articles[:4]:
            ctype = art.get("content_type", "?").upper()
            wc    = art.get("estimated_word_count", "?")
            print(f"\n        📄  {art.get('title', '—')[:65]}")
            print(f"            Type  : {ctype}  |  ~{wc} words")
            print(f"            ✓ {art.get('strengths', '—')[:70]}")
            print(f"            ✗ {art.get('weaknesses', '—')[:70]}")
        if len(articles) > 4:
            print(f"\n        ... +{len(articles)-4} more articles not shown")

    _section("COVERAGE GAPS  (topics ALL competitors miss — our biggest opportunities)")
    gaps = analysis.get("coverage_gaps", [])
    if gaps:
        for gap in gaps:
            pri   = gap.get("priority", "?").upper()
            icon  = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(pri, "⚪")
            stype = gap.get("suggested_article_type", "?")
            print(f"\n    {icon} [{pri}]  {gap.get('gap', '—')}")
            print(f"             Suggested type: {stype}")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    _section("OUR ADVANTAGES  (where Canvas Homes can differentiate)")
    advantages = analysis.get("our_advantages", [])
    if advantages:
        for adv in advantages:
            print(f"    ✨  {adv}")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    _section("RUN COST")
    _bullet("LLM cost", f"${data.get('cost_usd', 0):.4f}")
    print()


def print_keyword_mapper_output(data):
    """Full presentation of Keyword Mapper results."""
    _box("🗺️  KEYWORD MAPPER — OUTPUT REPORT")

    km = data.get("keyword_map", {})

    _section("WHAT THIS AGENT DID")
    print("    Took all PAA questions, related searches, autocomplete suggestions,")
    print("    and competitor article titles — merged into a structured keyword plan")
    print("    grouped by content piece. Each group = one article to write.")

    _section("SUMMARY")
    _bullet("Topic",          data.get("topic", "—"))
    _bullet("Total keywords", km.get("total_keywords", 0))
    _bullet("Keyword groups", f"{len(km.get('keyword_groups', []))} (each group = one article)")

    _section("KEYWORD GROUPS  (each group = one article Canvas Homes should publish)")
    groups = km.get("keyword_groups", [])
    if groups:
        for group in groups:
            pri   = group.get("priority", "?").upper()
            diff  = group.get("difficulty", "?").upper()
            vol   = group.get("estimated_volume", "?")
            atype = group.get("suggested_article_type", "?")
            icon  = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(pri, "⚪")
            print(f"\n    {icon} {group.get('group_name', '—')}")
            print(f"       Article type : {atype}  |  Difficulty: {diff}  |  Volume: {vol}")
            print(f"       Primary KW   : {group.get('primary_keyword', '—')}")
            sec = group.get("secondary_keywords", [])
            if sec:
                print(f"       Secondary    : {', '.join(sec[:4])}")
            lt = group.get("long_tail_keywords", [])
            if lt:
                print(f"       Long-tail    : {lt[0][:70]}")
                if len(lt) > 1:
                    print(f"                      +{len(lt)-1} more")
            faq = group.get("faq_keywords", [])
            if faq:
                print(f"       FAQ targets  : {faq[0][:70]}")
                if len(faq) > 1:
                    print(f"                      +{len(faq)-1} more")
    else:
        print("    ⚠️  Empty — LLM JSON was truncated. Confirm llm.py is the latest version.")

    qw = km.get("quick_win_keywords", [])
    if qw:
        _section("⚡ QUICK WINS  (low difficulty + decent volume — write these articles first)")
        _numbered(qw, indent=4, limit=10)

    sk = km.get("strategic_keywords", [])
    if sk:
        _section("♟️  STRATEGIC KEYWORDS  (high volume, high difficulty — need pillar content)")
        _numbered(sk, indent=4, limit=10)

    _section("RUN COST")
    _bullet("LLM cost", f"${data.get('cost_usd', 0):.4f}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# ARCHITECT INPUT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def build_architect_input(topic, trend_data, comp_data, keyword_data):
    """
    Package output of all three market intel agents into the exact input
    packet that Content Architect and FAQ Architect will receive.
    """
    analysis = trend_data.get("analysis", {})
    raw      = trend_data.get("raw_data", {})
    km       = keyword_data.get("keyword_map", {})
    clf      = trend_data.get("classification", {})

    return {
        "topic":             topic,
        "topic_type":        clf.get("primary_category", "locality"),
        "detected_locality": (clf.get("detected_localities", [topic]) or [topic])[0],

        "trend": {
            "direction":      analysis.get("trend_direction", "unknown"),
            "summary":        analysis.get("trend_summary", ""),
            "seasonal":       raw.get("trends", {}).get("is_seasonal", False),
            "rising_queries": raw.get("trends", {}).get("rising_queries", [])[:10],
        },

        "keyword_groups":     km.get("keyword_groups", []),
        "quick_win_keywords": km.get("quick_win_keywords", []),
        "strategic_keywords": km.get("strategic_keywords", []),
        "total_keywords":     km.get("total_keywords", 0),

        "aeo_targets": analysis.get("aeo_targets", []),

        "faq": {
            "paa_questions":    raw.get("paa_questions", []),
            "related_searches": raw.get("related_searches", [])[:30],
            "autocomplete":     raw.get("autocomplete", [])[:20],
        },

        "competition": {
            "competitor_coverage": comp_data.get("analysis", {}).get("competitor_coverage", []),
            "coverage_gaps":       comp_data.get("analysis", {}).get("coverage_gaps", []),
            "our_advantages":      comp_data.get("analysis", {}).get("our_advantages", []),
            "competitor_presence": trend_data.get("competitor_tracker", {}),
        },

        "content_priorities": {
            "top_5_queries":  analysis.get("top_5_priority_queries", []),
            "content_gaps":   analysis.get("content_gaps", []),
            "related_topics": analysis.get("related_topics_to_explore", []),
        },
    }


def print_architect_input(packet):
    """Display the architect input packet — what the next agent layer receives."""
    _box("🏗️  NEXT LAYER INPUT — CONTENT ARCHITECT + FAQ ARCHITECT")

    print("""
  This is the exact JSON packet passed to the next agents.
  Content Architect reads this → produces article briefs.
  FAQ Architect reads this     → produces FAQ page structures.
  """)

    _section("IDENTITY")
    _bullet("Topic",    packet["topic"])
    _bullet("Type",     packet["topic_type"])
    _bullet("Locality", packet["detected_locality"])

    _section("TREND CONTEXT  → Content Architect uses this to set article angle")
    t = packet["trend"]
    _bullet("Direction",      t["direction"])
    _bullet("Summary",        (t["summary"][:100] + "...") if len(t.get("summary", "")) > 100 else t.get("summary", "—"))
    _bullet("Seasonal",       t["seasonal"])
    _bullet("Rising queries", f"{len(t['rising_queries'])} found")
    for q in t["rising_queries"][:3]:
        print(f"        – {q}")

    _section(f"KEYWORD GROUPS  → Content Architect turns each into one article brief")
    groups = packet["keyword_groups"]
    if groups:
        print(f"    {len(groups)} groups = {len(groups)} articles to commission\n")
        for i, g in enumerate(groups, 1):
            pri  = g.get("priority", "?").upper()
            icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(pri, "⚪")
            print(f"    {icon} Article {i}: {g.get('group_name', '—')}")
            print(f"       Type     : {g.get('suggested_article_type', '?')}")
            print(f"       Primary  : {g.get('primary_keyword', '—')}")
            print(f"       Priority : {pri}  |  Difficulty: {g.get('difficulty','?').upper()}  |  Volume: {g.get('estimated_volume','?')}")
    else:
        print("    ⚠️  No groups — run with latest llm.py to populate.")

    if packet["quick_win_keywords"]:
        _section("⚡ QUICK WINS  → Content Architect writes these first")
        for kw in packet["quick_win_keywords"][:5]:
            print(f"    – {kw}")

    _section("AEO TARGETS  → FAQ Architect writes AI-optimised answers for these")
    aeo = packet["aeo_targets"]
    if aeo:
        print(f"    {len(aeo)} targets where we can become the AI-cited answer\n")
        for t in aeo[:5]:
            print(f"    🎯  {t.get('query','—')}")
            print(f"        Current quality : {t.get('current_answer_quality','—')}")
            print(f"        Our strategy    : {t.get('our_strategy','—')[:80]}")
    else:
        print("    ⚠️  No AEO targets — run with latest llm.py to populate.")

    _section("FAQ SOURCE MATERIAL  → FAQ Architect maps these into Q&A sections")
    faq = packet["faq"]
    _bullet("PAA questions",    f"{len(faq['paa_questions'])} real Google PAA questions")
    _bullet("Related searches", f"{len(faq['related_searches'])} terms")
    _bullet("Autocomplete",     f"{len(faq['autocomplete'])} suggestions")
    print(f"\n    Sample PAA questions FAQ Architect will answer:")
    for q in faq["paa_questions"][:5]:
        print(f"        ❓  {q}")

    _section("COMPETITIVE CONTEXT  → sets differentiation angle in every brief")
    comp = packet["competition"]
    _bullet("Competitors tracked", len(comp["competitor_presence"]))
    _bullet("Coverage gaps found", len(comp["coverage_gaps"]))
    _bullet("Our advantages",      len(comp["our_advantages"]))
    if comp["coverage_gaps"]:
        g = comp["coverage_gaps"][0]
        print(f"\n    Top gap for Content Architect to exploit:")
        print(f"    🔴  {g.get('gap','—')}")

    _section("CONTENT PRIORITIES  → Content Architect writes in this order")
    top5 = packet["content_priorities"]["top_5_queries"]
    if top5:
        for i, q in enumerate(top5, 1):
            print(f"    {i}. {q.get('query','—')}")
            print(f"       → {q.get('suggested_action','—')[:80]}")
    else:
        print("    ⚠️  Empty — run with latest llm.py to populate.")

    _section("FULL JSON PACKET  (this is what Content Architect and FAQ Architect receive)")
    print()
    print(json.dumps(packet, indent=2, ensure_ascii=False))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CACHE KEY HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _get_cache_key_for_topic(topic, classification):
    """
    Reconstruct the exact serp_v2 cache key that _search_serp_enhanced() writes
    for the first query generated for this topic.

    WHAT WAS WRONG (previous version):
        The cache key was built as f"serp_v2:<md5('{TOPIC} Bangalore')>" using
        the raw TOPIC string. But Trend Scout classifies the topic and extracts
        the detected locality name (e.g. "Electronic City" from
        "ELECTRONIC CITY BANGALORE"). The first query it actually runs is
        "{detected_locality} Bangalore" in the agent's original casing, NOT the
        raw TOPIC string. When TOPIC was typed in all caps the hash mismatched
        and the cache check always failed.

    WHAT WAS DONE TO FIX IT:
        Extract the detected locality from the classification data that Trend
        Scout already returns, then build the first query the same way
        _generate_queries_locality() does: "{loc} Bangalore". This matches the
        actual cache key regardless of how the user typed the TOPIC.
    """
    detected = classification.get("detected_localities", [])
    if detected:
        # Use the locality name exactly as Trend Scout detected it (mixed case)
        loc = detected[0]
    else:
        # Fallback: title-case the topic
        loc = topic.title()

    first_query = f"{loc} Bangalore"
    return f"serp_v2:{hashlib.md5(first_query.encode()).hexdigest()}", first_query


# ─────────────────────────────────────────────────────────────────────────────
# TEST HARNESS
# ─────────────────────────────────────────────────────────────────────────────

print("=== TEST: Market Intelligence Agents ===")
print("  (This test makes real API calls — uses ~15 SerpAPI searches + ~$0.02 LLM)")
passed = 0
failed = 0

def check(label, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ {label}")
        passed += 1
    else:
        print(f"  ❌ {label} {detail}")
        failed += 1

# ── Change this to any Bangalore locality or topic ──────────────────────────
TOPIC = "hennur"
# ────────────────────────────────────────────────────────────────────────────

# ─── Trend Scout ───
print("\n--- Trend Scout ---")
from agents.trend_scout import run_trend_scout
try:
    trend_data = run_trend_scout(TOPIC)
    check("Trend Scout returns data", trend_data is not None)
    check("Has topic field",  trend_data.get("topic") == TOPIC)
    check("Has raw_data",     "raw_data" in trend_data)
    check("Has analysis",     "analysis" in trend_data)
    raw = trend_data.get("raw_data", {})
    check("Found PAA questions",    len(raw.get("paa_questions", [])) >= 1,
          f"— Got {len(raw.get('paa_questions', []))}")
    check("Found related searches", len(raw.get("related_searches", [])) >= 1)
    check("Found autocomplete",     len(raw.get("autocomplete", [])) >= 1)
    check("Cost tracked",           trend_data.get("cost_usd", 0) > 0)
except Exception as e:
    check("Trend Scout execution", False, f"— {e}")
    trend_data = {
        "analysis": {}, "topic": TOPIC,
        "raw_data": {"paa_questions": [], "related_searches": [], "autocomplete": []},
        "classification": {}, "competitor_tracker": {},
    }

# ─── Competitor Spy ───
print("\n--- Competitor Spy ---")
from agents.competitor_spy import run_competitor_spy
try:
    comp_data = run_competitor_spy(TOPIC)
    check("Competitor Spy returns data",   comp_data is not None)
    check("Has raw_results",               "raw_results" in comp_data)
    check("Has analysis",                  "analysis" in comp_data)
    raw = comp_data.get("raw_results", {})
    check("Searched multiple competitors", len(raw) >= 2,
          f"— Searched {len(raw)} competitors")
    check("Found competitor content",
          any(len(v) > 0 for v in raw.values()),
          "— No results from any competitor")
except Exception as e:
    check("Competitor Spy execution", False, f"— {e}")
    comp_data = {"analysis": {}, "raw_results": {}}

# ─── Keyword Mapper ───
print("\n--- Keyword Mapper ---")
from agents.keyword_mapper import run_keyword_mapper
try:
    keyword_data = run_keyword_mapper(TOPIC, trend_data, comp_data)
    check("Keyword Mapper returns data", keyword_data is not None)
    check("Has keyword_map",             "keyword_map" in keyword_data)
    km = keyword_data.get("keyword_map", {})
    check("Has keyword groups",     len(km.get("keyword_groups", [])) >= 1,
          f"— Got {len(km.get('keyword_groups', []))}")
    check("Total keywords counted", km.get("total_keywords", 0) >= 5,
          f"— Got {km.get('total_keywords', 0)}")
except Exception as e:
    check("Keyword Mapper execution", False, f"— {e}")
    keyword_data = {"keyword_map": {}}

# ─── Caching ───
print("\n--- Caching ---")
from db.sqlite_ops import cache_get

# WHAT WAS WRONG:
#   Previous version used the raw TOPIC string to build the cache key.
#   When TOPIC was "ELECTRONIC CITY BANGALORE" (all caps), the hash was
#   md5("ELECTRONIC CITY BANGALORE Bangalore") which never matched the
#   actual cache key md5("Electronic City Bangalore") that Trend Scout
#   wrote (using the detected locality name in its original mixed case).
#
# WHAT WAS DONE TO FIX IT:
#   _get_cache_key_for_topic() extracts the detected locality from the
#   classification data Trend Scout already returned, then builds the
#   first query exactly as the agent does — matching always.

clf       = trend_data.get("classification", {})
cache_key, first_query = _get_cache_key_for_topic(TOPIC, clf)
cached    = cache_get(cache_key)
check("SerpAPI results cached", cached is not None,
      f"— Cache miss for serp_v2:<md5('{first_query}')>")

# ─── Agent Run Tracking ───
print("\n--- Agent Run Tracking ---")
from db.sqlite_ops import get_agent_runs
trend_runs = get_agent_runs(agent_name="trend_scout", limit=1)
check("Trend Scout run recorded", len(trend_runs) >= 1)
check("Run has cost data",
      trend_runs[0].get("cost_usd", 0) > 0 if trend_runs else False)
comp_runs = get_agent_runs(agent_name="competitor_spy", limit=1)
check("Competitor Spy run recorded", len(comp_runs) >= 1)
kw_runs = get_agent_runs(agent_name="keyword_mapper", limit=1)
check("Keyword Mapper run recorded", len(kw_runs) >= 1)

# ─────────────────────────────────────────────────────────────────────────────
# PRESENT FULL AGENT OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

print_trend_scout_output(trend_data)
print_competitor_spy_output(comp_data)
print_keyword_mapper_output(keyword_data)

# ─────────────────────────────────────────────────────────────────────────────
# BUILD AND DISPLAY ARCHITECT INPUT
# ─────────────────────────────────────────────────────────────────────────────

architect_input = build_architect_input(TOPIC, trend_data, comp_data, keyword_data)
print_architect_input(architect_input)

# Save so Content Architect and FAQ Architect can load directly
os.makedirs("outputs", exist_ok=True)
safe_name = TOPIC.lower().replace(" ", "_").replace("/", "_")
architect_input_path = f"outputs/architect_input_{safe_name}.json"
with open(architect_input_path, "w", encoding="utf-8") as f:
    json.dump(architect_input, f, indent=2, ensure_ascii=False)
print(f"  💾  Architect input saved to: {architect_input_path}")
print(f"      Content Architect and FAQ Architect load this file directly.\n")

# ─────────────────────────────────────────────────────────────────────────────
# FINAL RESULT
# ─────────────────────────────────────────────────────────────────────────────

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ MARKET INTEL TEST FAILED")
    sys.exit(1)
else:
    print("✅ MARKET INTEL TEST PASSED")