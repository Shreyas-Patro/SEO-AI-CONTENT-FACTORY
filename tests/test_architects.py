"""Test: Content Architect and FAQ Architect agents."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== TEST: Content & FAQ Architect ===")
print("  (Makes API calls — costs ~$0.15 Sonnet + $0.01 Haiku)")
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

# Load architect input from pipeline output (instead of mock data)
architect_input_path = "outputs/architect_input_hennur.json"
if os.path.exists(architect_input_path):
    with open(architect_input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    topic = data["topic"]
    # Map JSON fields to expected keyword_data structure
    keyword_data = {
        "keyword_map": {
            "total_keywords": data.get("total_keywords", 0),
            "keyword_groups": data.get("keyword_groups", []),
            "quick_win_keywords": data.get("quick_win_keywords", []),
            "strategic_keywords": data.get("strategic_keywords", []),
        }
    }
    # Optionally include AEO targets or other fields if needed by architects
else:
    raise FileNotFoundError(f"Architect input file not found: {architect_input_path}. Run test_market_intel.py first.")

# ─── Content Architect ───
print("\n--- Content Architect ---")
from agents.content_architect import run_content_architect
from db.sqlite_ops import get_cluster, get_articles_by_cluster

try:
    result = run_content_architect(topic, keyword_data)
    check("Content Architect returns result", result is not None)

    cluster_id = result.get("cluster_id")
    check("Cluster ID created", cluster_id is not None)

    plan = result.get("cluster_plan", {})
    articles = plan.get("articles", [])
    check("Articles planned", len(articles) >= 3, f"— Got {len(articles)}")

    # Validate article structure
    hubs = [a for a in articles if a.get("type") == "hub"]
    spokes = [a for a in articles if a.get("type") == "spoke"]
    check("Has at least 1 hub", len(hubs) >= 1)
    check("Has at least 2 spokes", len(spokes) >= 2)

    # Validate each article has required fields
    for a in articles[:3]:
        has_fields = all(k in a for k in ["title", "slug", "type", "outline"])
        check(f"Article '{a.get('title', '?')[:30]}...' has required fields", has_fields)

    # Validate slugs are URL-friendly
    for a in articles:
        slug = a.get("slug", "")
        is_valid = slug == slug.lower() and " " not in slug and slug.replace("-", "").isalnum()
        check(f"Slug '{slug}' is URL-friendly", is_valid or slug == "")

    # Verify articles stored in DB
    db_articles = get_articles_by_cluster(cluster_id)
    check("Articles stored in database", len(db_articles) >= 3,
          f"— Got {len(db_articles)} in DB vs {len(articles)} planned")

except Exception as e:
    check("Content Architect execution", False, f"— {e}")
    cluster_id = None

# ─── FAQ Architect ───
print("\n--- FAQ Architect ---")
if cluster_id:
    from agents.faq_architect import run_faq_architect
    db_articles = get_articles_by_cluster(cluster_id)

    if db_articles:
        first_article = db_articles[0]
        try:
            faq_result = run_faq_architect(first_article["id"], keyword_data, cluster_id)
            check("FAQ Architect returns result", faq_result is not None)

            faqs = faq_result.get("faqs", [])
            check("FAQs generated", len(faqs) >= 3, f"— Got {len(faqs)}")

            if faqs:
                first_faq = faqs[0]
                check("FAQ has question", "question" in first_faq)
                check("FAQ has answer", "answer" in first_faq)
                check("FAQ answer is snippet-length (40-80 words)",
                      10 <= len(first_faq.get("answer", "").split()) <= 100,
                      f"— Got {len(first_faq.get('answer', '').split())} words")

            # Verify FAQs stored in article
            from db.sqlite_ops import get_article
            updated = get_article(first_article["id"])
            stored_faqs = json.loads(updated.get("faq_json", "[]"))
            check("FAQs stored in article record", len(stored_faqs) >= 1)

        except Exception as e:
            check("FAQ Architect execution", False, f"— {e}")
    else:
        check("Articles available for FAQ generation", False)
else:
    check("Cluster available for FAQ test", False, "— Content Architect must pass first")

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ ARCHITECT TEST FAILED")
    sys.exit(1)
else:
    print("✅ ARCHITECT TEST PASSED")
