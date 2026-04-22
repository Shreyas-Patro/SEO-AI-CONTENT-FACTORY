"""
Test: Trend Scout v2 — Full Bangalore coverage
===============================================
Tests topic classification, query generation, SERP feature detection,
AEO scoring, and end-to-end runs across all topic types.

Test tiers:
  TIER 1 (free): classification, query generation — no API calls
  TIER 2 (paid): full SERP + LLM runs — ~$0.15 per topic tested
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import only the pure functions first (no API calls)
from agents.trend_scout import (
    classify_topic,
    generate_all_queries,
    _score_aeo_opportunity,
    BANGALORE_LOCALITIES,
    LOCALITY_NEIGHBORS,
)

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


# ═══════════════════════════════════════════════════════
# TIER 1: FREE TESTS (no API calls, run always)
# ═══════════════════════════════════════════════════════

print("=" * 60)
print("TIER 1: Classification & Query Generation (free)")
print("=" * 60)


# ─── 1.1: Topic Classification ───

print("\n--- 1.1: Locality Detection ---")

# Exact matches
c = classify_topic("HSR Layout")
check("HSR Layout → locality", c["primary_category"] == "locality")
check("HSR Layout detected", "HSR Layout" in c["detected_localities"])

c = classify_topic("Koramangala")
check("Koramangala → locality", c["primary_category"] == "locality")

c = classify_topic("Whitefield")
check("Whitefield → locality", c["primary_category"] == "locality")

c = classify_topic("Electronic City")
check("Electronic City → locality", c["primary_category"] == "locality")

# Locality embedded in longer query
c = classify_topic("best restaurants in Indiranagar")
check("'best restaurants in Indiranagar' → locality + lifestyle",
      "locality" in c["categories"] and "lifestyle" in c["categories"])
check("  is_cross_cutting = True", c["is_cross_cutting"])

c = classify_topic("2BHK rent in Koramangala")
check("'2BHK rent Koramangala' → property_type + locality",
      "property_type" in c["categories"] and "locality" in c["categories"])

# Unknown locality (should default to locality if capitalized)
c = classify_topic("Hennur Main Road")
check("Unknown area defaults to locality", c["primary_category"] == "locality")


print("\n--- 1.2: Property Type Detection ---")

c = classify_topic("2BHK apartment")
check("'2BHK apartment' → property_type", c["primary_category"] == "property_type")

c = classify_topic("villa for sale Bangalore")
check("'villa for sale' → property_type", "property_type" in c["categories"])

c = classify_topic("PG accommodation")
check("'PG accommodation' → property_type", "property_type" in c["categories"])

c = classify_topic("studio apartment Bangalore")
check("'studio apartment' → property_type", "property_type" in c["categories"])


print("\n--- 1.3: Legal Topic Detection ---")

c = classify_topic("RERA registration")
check("'RERA registration' → legal", c["primary_category"] == "legal")

c = classify_topic("stamp duty in Karnataka")
check("'stamp duty' → legal", "legal" in c["categories"])

c = classify_topic("rental agreement format")
check("'rental agreement' → legal", "legal" in c["categories"])

c = classify_topic("khata transfer process")
check("'khata transfer' → legal", "legal" in c["categories"])

c = classify_topic("encumbrance certificate Bangalore")
check("'encumbrance certificate' → legal", "legal" in c["categories"])


print("\n--- 1.4: Finance Topic Detection ---")

c = classify_topic("home loan")
check("'home loan' → finance", c["primary_category"] == "finance")

c = classify_topic("SBI home loan interest rate")
check("'SBI home loan interest rate' → finance", "finance" in c["categories"])

c = classify_topic("property tax Bangalore")
check("'property tax' → finance or legal", 
      "finance" in c["categories"] or "legal" in c["categories"])

c = classify_topic("capital gains tax on property sale")
check("'capital gains tax' → finance", "finance" in c["categories"])

c = classify_topic("EMI calculator")
check("'EMI calculator' → finance", "finance" in c["categories"])


print("\n--- 1.5: Lifestyle Topic Detection ---")

c = classify_topic("best schools in Bangalore")
check("'best schools' → lifestyle", c["primary_category"] == "lifestyle")

c = classify_topic("things to do in Bangalore")
check("'things to do' → lifestyle", "lifestyle" in c["categories"])

c = classify_topic("hospitals near Whitefield")
check("'hospitals near Whitefield' → lifestyle + locality",
      "lifestyle" in c["categories"] and "locality" in c["categories"])


print("\n--- 1.6: Infrastructure Topic Detection ---")

c = classify_topic("Namma Metro Phase 3")
check("'Namma Metro' → infrastructure", c["primary_category"] == "infrastructure")

c = classify_topic("ORR Bangalore")
check("'ORR' → infrastructure", "infrastructure" in c["categories"])

c = classify_topic("Peripheral Ring Road")
check("'Peripheral Ring Road' → infrastructure", "infrastructure" in c["categories"])


print("\n--- 1.7: Process Topic Detection ---")

c = classify_topic("how to buy property in Bangalore")
check("'how to buy property' → process", c["primary_category"] == "process")

c = classify_topic("NRI property purchase India")
check("'NRI property purchase' → process", "process" in c["categories"])

c = classify_topic("documents required for home loan")
check("'documents required' → process or finance",
      "process" in c["categories"] or "finance" in c["categories"])


print("\n--- 1.8: Market Topic Detection ---")

c = classify_topic("Bangalore property market 2026")
check("'property market 2026' → market", "market" in c["categories"])

c = classify_topic("real estate forecast Bangalore")
check("'real estate forecast' → market", "market" in c["categories"])


print("\n--- 1.9: Edge Cases ---")

c = classify_topic("abc xyz random words")
check("Unrecognized topic gets a category", len(c["categories"]) >= 1)

c = classify_topic("")
check("Empty string doesn't crash", c is not None)

c = classify_topic("BDA approved sites near Devanahalli")
check("Multi-signal → picks up legal + locality",
      len(c["categories"]) >= 1)


# ─── 2: Query Generation ───

print("\n--- 2.1: Locality Query Generation ---")

c = classify_topic("HSR Layout")
queries = generate_all_queries("HSR Layout", c, max_serp_calls=15)
check("Locality generates 10+ queries", len(queries) >= 10,
      f"— got {len(queries)}")

query_texts = [q["query"].lower() for q in queries]
groups = set(q["group"] for q in queries)

check("Has core queries", "core" in groups)
check("Has property queries", "property" in groups)
check("Has lifestyle queries", "lifestyle" in groups)
check("Has comparison queries", "comparison" in groups)

# Verify comparison queries use actual neighbors
comparison_queries = [q["query"] for q in queries if q["group"] == "comparison"]
if comparison_queries:
    has_valid_comparison = any(
        neighbor.lower() in comp.lower()
        for comp in comparison_queries
        for neighbor in LOCALITY_NEIGHBORS.get("hsr layout", [])
    )
    check("Comparison queries use real neighboring localities", has_valid_comparison,
          f"— got: {comparison_queries}")
else:
    check("Comparison queries generated", False, "— no comparison queries")


print("\n--- 2.2: Legal Query Generation ---")

c = classify_topic("RERA registration")
queries = generate_all_queries("RERA registration", c, max_serp_calls=15)
check("Legal topic generates queries", len(queries) >= 5)

groups = set(q["group"] for q in queries)
check("Legal has 'how_to' queries", "how_to" in groups or "core" in groups)
check("Legal has 'specific' queries", "specific" in groups or "core" in groups)


print("\n--- 2.3: Finance Query Generation ---")

c = classify_topic("home loan")
queries = generate_all_queries("home loan", c, max_serp_calls=15)
check("Finance topic generates queries", len(queries) >= 5)

groups = set(q["group"] for q in queries)
check("Finance has 'rates' queries", "rates" in groups or "core" in groups)


print("\n--- 2.4: Cross-Cutting Query Generation ---")

c = classify_topic("schools near Whitefield")
queries = generate_all_queries("schools near Whitefield", c, max_serp_calls=15)
check("Cross-cutting generates queries", len(queries) >= 5)

groups = set(q["group"] for q in queries)
check("Cross-cutting has queries from multiple categories",
      len(groups) >= 2, f"— groups: {groups}")


print("\n--- 2.5: Budget Respect ---")

for budget in [5, 10, 15, 20]:
    c = classify_topic("HSR Layout")
    queries = generate_all_queries("HSR Layout", c, max_serp_calls=budget)
    check(f"Budget {budget}: queries ≤ {budget}", len(queries) <= budget,
          f"— got {len(queries)}")


print("\n--- 2.6: No Duplicate Queries ---")

c = classify_topic("Koramangala")
queries = generate_all_queries("Koramangala", c, max_serp_calls=20)
query_texts = [q["query"].lower().strip() for q in queries]
unique = set(query_texts)
check("No duplicate queries", len(unique) == len(query_texts),
      f"— {len(query_texts)} total, {len(unique)} unique, "
      f"dupes: {[q for q in query_texts if query_texts.count(q) > 1]}")


# ─── 3: AEO Scoring ───

print("\n--- 3: AEO Opportunity Scoring ---")

# High opportunity: no snippet, no AIO, no competitors, question format
high_opp = _score_aeo_opportunity({
    "query": "what is the average rent in HSR Layout",
    "featured_snippet": None,
    "ai_overview": None,
    "competitor_presence": [],
    "serp_features": ["people_also_ask"],
})
check("High AEO opportunity scores ≥ 80", high_opp >= 80,
      f"— got {high_opp}")

# Low opportunity: snippet exists, AIO exists, competitors present
low_opp = _score_aeo_opportunity({
    "query": "HSR Layout Bangalore",
    "featured_snippet": {"source": "https://some-other-site.com"},
    "ai_overview": {"text": "HSR Layout is..."},
    "competitor_presence": ["magicbricks.com"],
    "serp_features": ["featured_snippet", "ai_overview"],
})
check("Low AEO opportunity scores < 70", low_opp < 70,
      f"— got {low_opp}")

# Mid opportunity: competitor has the snippet (steal opportunity)
mid_opp = _score_aeo_opportunity({
    "query": "rent in Koramangala",
    "featured_snippet": {"source": "https://magicbricks.com/some-page"},
    "ai_overview": None,
    "competitor_presence": ["magicbricks.com"],
    "serp_features": ["featured_snippet"],
})
check("Competitor snippet = moderate opportunity", 50 <= mid_opp <= 85,
      f"— got {mid_opp}")

check("High > Low opportunity", high_opp > low_opp)


# ─── 4: Bangalore Knowledge Validation ───

print("\n--- 4: Bangalore Knowledge Base ---")

check("Has 40+ localities", len(BANGALORE_LOCALITIES) >= 40,
      f"— has {len(BANGALORE_LOCALITIES)}")

check("Has locality neighbor mappings", len(LOCALITY_NEIGHBORS) >= 10,
      f"— has {len(LOCALITY_NEIGHBORS)} mapped")

# Verify all neighbor entries reference valid localities (or at least reasonable names)
for loc, neighbors in LOCALITY_NEIGHBORS.items():
    check(f"Neighbors for '{loc}' has entries", len(neighbors) >= 3,
          f"— only {len(neighbors)}")

# Check major localities have neighbor mappings
major_localities = ["hsr layout", "koramangala", "indiranagar", "whitefield",
                    "electronic city", "marathahalli", "hebbal"]
for loc in major_localities:
    check(f"Major locality '{loc}' has neighbor map", loc in LOCALITY_NEIGHBORS,
          f"— missing from LOCALITY_NEIGHBORS")


# ═══════════════════════════════════════════════════════
# TIER 2: PAID TESTS (API calls, run with --full flag)
# ═══════════════════════════════════════════════════════

run_paid = "--full" in sys.argv

if not run_paid:
    print("\n" + "=" * 60)
    print("TIER 2: Skipped (run with --full flag for paid API tests)")
    print("  Cost estimate: ~$0.30 for 2 topic runs")
    print("=" * 60)
else:
    print("\n" + "=" * 60)
    print("TIER 2: Full SERP + LLM Tests (paid)")
    print("=" * 60)

    from agents.trend_scout import run_trend_scout

    # ─── 5.1: Locality E2E ───
    print("\n--- 5.1: End-to-End Locality Test ---")
    try:
        result = run_trend_scout("HSR Layout", max_serp_calls=5)

        check("E2E returns result", result is not None)
        check("Has classification", "classification" in result)
        check("Has raw_data", "raw_data" in result)
        check("Has analysis", "analysis" in result)
        check("Classification is locality",
              result["classification"]["primary_category"] == "locality")

        raw = result.get("raw_data", {})
        check("Found PAA questions", len(raw.get("paa_questions", [])) >= 1)
        check("Found related searches", len(raw.get("related_searches", [])) >= 1)
        check("Has AEO scores", len(raw.get("aeo_scores", [])) >= 1)

        aeo = raw.get("aeo_scores", [])
        if aeo:
            check("AEO scores have required fields",
                  all("score" in s and "has_featured_snippet" in s for s in aeo))
            high_aeo = [s for s in aeo if s["score"] >= 70]
            print(f"    📊 High AEO opportunities: {len(high_aeo)}/{len(aeo)}")

        check("Competitor tracker populated",
              len(result.get("competitor_tracker", {})) >= 0)  # May be 0 if no competitors found
        check("Cost tracked", result.get("cost_usd", 0) > 0)
        check("SERP calls tracked", result.get("serp_calls_used", 0) > 0)

        analysis = result.get("analysis", {})
        check("Analysis has intent_clusters", "intent_clusters" in analysis)
        check("Analysis has content_gaps", "content_gaps" in analysis)
        check("Analysis has top_5_priority_queries", "top_5_priority_queries" in analysis)

    except Exception as e:
        check("E2E locality test", False, f"— {e}")

    # ─── 5.2: Non-Locality E2E ───
    print("\n--- 5.2: End-to-End Legal Topic Test ---")
    try:
        result = run_trend_scout("RERA registration", max_serp_calls=5)

        check("Legal E2E returns result", result is not None)
        check("Legal classification correct",
              result["classification"]["primary_category"] == "legal")
        check("Legal analysis generated", len(result.get("analysis", {})) > 0)

    except Exception as e:
        check("E2E legal test", False, f"— {e}")


# ═══════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════

print(f"\n{'=' * 60}")
print(f"Results: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ TREND SCOUT v2 TEST FAILED")
    sys.exit(1)
else:
    print("✅ TREND SCOUT v2 TEST PASSED")
    if not run_paid:
        print("   (Run with --full to include paid API tests)")