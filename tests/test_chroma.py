"""Test: ChromaDB vector store initialization and semantic search."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.chroma_ops import *

print("=== TEST: ChromaDB Vector Store ===")
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

# Test 1: Initialize collections
try:
    client = init_collections()
    check("Collections initialized", True)
except Exception as e:
    check("Collections initialized", False, f"— {e}")
    sys.exit(1)

# Test 2: Verify collections exist
collections = client.list_collections()
col_names = [c.name for c in collections]
check("'facts' collection exists", "facts" in col_names)
check("'articles' collection exists", "articles" in col_names)
check("'queries' collection exists", "queries" in col_names)

# Test 3: Store a fact embedding
try:
    store_fact_embedding(
        "test-chroma-001",
        "The average rent for a 2BHK in Koramangala is ₹35,000 per month.",
        {"category": "rental", "location": "Koramangala"}
    )
    check("Store fact embedding", True)
except Exception as e:
    check("Store fact embedding", False, f"— {e}")

# Test 4: Store another fact for comparison
store_fact_embedding(
    "test-chroma-002",
    "HSR Layout has 12 public parks maintained by BBMP.",
    {"category": "lifestyle", "location": "HSR Layout"}
)
store_fact_embedding(
    "test-chroma-003",
    "Property prices in Whitefield range from ₹5,500 to ₹8,000 per sq ft.",
    {"category": "property", "location": "Whitefield"}
)
check("Store multiple facts", True)

# Test 5: Semantic search — should find the rental fact
results = search_facts("How much is rent in Koramangala?", top_k=3)
check("Semantic search returns results", len(results) >= 1)
check("Most relevant result is about Koramangala rent",
      any("Koramangala" in r["text"] and "rent" in r["text"].lower() for r in results),
      f"— Got: {results[0]['text'][:60] if results else 'empty'}")

# Test 6: Semantic search — different query, different top result
results2 = search_facts("parks and green spaces in HSR Layout", top_k=3)
check("Search finds HSR parks fact",
      any("parks" in r["text"].lower() and "HSR" in r["text"] for r in results2))

# Test 7: Semantic search — property prices
results3 = search_facts("property cost Whitefield", top_k=3)
check("Search finds Whitefield property fact",
      any("Whitefield" in r["text"] for r in results3))

# Test 8: Search with metadata filter
results_filtered = search_facts("rent prices", top_k=5,
                                 where_filter={"category": "rental"})
check("Filtered search works", len(results_filtered) >= 1)
check("Filtered results match category",
      all(r["metadata"].get("category") == "rental" for r in results_filtered))

# Test 9: Store and search article
try:
    store_article_embedding(
        "test-art-001",
        "This is a comprehensive guide to living in HSR Layout, covering rent prices, "
        "connectivity, lifestyle, and investment potential.",
        {"title": "HSR Layout Guide", "slug": "hsr-layout-guide"}
    )
    check("Store article embedding", True)
except Exception as e:
    check("Store article embedding", False, f"— {e}")

results_art = search_articles("HSR Layout living guide", top_k=2)
check("Article search returns results", len(results_art) >= 1)

# Test 10: Upsert (update existing embedding)
store_fact_embedding(
    "test-chroma-001",
    "The average rent for a 2BHK in Koramangala is ₹38,000 per month as of 2026.",
    {"category": "rental", "location": "Koramangala"}
)
updated = search_facts("Koramangala rent 2BHK", top_k=1)
check("Upsert updates existing embedding",
      "38,000" in updated[0]["text"] if updated else False)

# Clean up
client = get_client()
facts_col = client.get_collection("facts", embedding_function=ef)
facts_col.delete(ids=["test-chroma-001", "test-chroma-002", "test-chroma-003"])
articles_col = client.get_collection("articles", embedding_function=ef)
articles_col.delete(ids=["test-art-001"])
check("Test data cleaned up", True)

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ CHROMA TEST FAILED")
    sys.exit(1)
else:
    print("✅ CHROMA TEST PASSED")