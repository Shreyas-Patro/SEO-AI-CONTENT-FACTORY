"""Test: SQLite database initialization and all CRUD operations."""
import sys, os, json, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.sqlite_ops import *

print("=== TEST: SQLite Database ===")
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

# ─── PRE-CLEANUP: Remove stale test data from previous crashed runs ───
try:
    init_db()
    conn = get_conn()
    conn.execute("DELETE FROM agent_runs WHERE agent_name LIKE 'test_%'")
    conn.execute("DELETE FROM verification_queue WHERE claim_text = 'Test claim'")
    conn.execute("DELETE FROM articles WHERE slug LIKE 'test-hsr-%'")
    conn.execute("DELETE FROM clusters WHERE name IN ('Test Cluster', 'Updated Cluster')")
    conn.execute("DELETE FROM facts WHERE content LIKE 'Test fact:%'")
    conn.execute("DELETE FROM sources WHERE title = 'Test Source'")
    conn.execute("DELETE FROM api_cache WHERE cache_key = 'test_key'")
    conn.commit()
    conn.close()
    check("Database initialized (stale data cleaned)", True)
except Exception as e:
    check("Database initialized", False, f"— {e}")
    sys.exit(1)

# Use a unique suffix so slugs never collide across parallel/repeated runs
_uid = uuid.uuid4().hex[:6]

# Test 2: Insert and retrieve a source
src_id = insert_source("https://test.com", "Test Source", "Author", "2025-01-01")
check("Insert source", src_id is not None and src_id.startswith("src-"))

# Test 3: Insert and retrieve a fact
fact_id = insert_fact(
    content="Test fact: Average rent in HSR Layout is ₹28,000/month",
    source_url="https://test.com",
    source_title="Test Source",
    category="rental",
    location="HSR Layout",
    confidence=0.95,
    source_id=src_id,
)
check("Insert fact", fact_id is not None and fact_id.startswith("fact-"))

fact = get_fact_by_id(fact_id)
check("Retrieve fact by ID", fact is not None)
check("Fact content matches", fact["content"] == "Test fact: Average rent in HSR Layout is ₹28,000/month")
check("Fact category correct", fact["category"] == "rental")
check("Fact location correct", fact["location"] == "HSR Layout")

# Test 4: Query facts by category
rental_facts = get_facts(category="rental")
check("Query facts by category", len(rental_facts) >= 1)

# Test 5: Query facts by location
hsr_facts = get_facts(location="HSR Layout")
check("Query facts by location", len(hsr_facts) >= 1)

# Test 6: Update fact
update_fact(fact_id, verified=1, confidence=0.99)
updated = get_fact_by_id(fact_id)
check("Update fact verified", updated["verified"] == 1)
check("Update fact confidence", updated["confidence"] == 0.99)

# Test 7: Create and retrieve cluster
cl_id = create_cluster("Test Cluster", "HSR Layout")
check("Create cluster", cl_id is not None and cl_id.startswith("cl-"))

cluster = get_cluster(cl_id)
check("Retrieve cluster", cluster is not None)
check("Cluster name matches", cluster["name"] == "Test Cluster")
check("Cluster status is planning", cluster["status"] == "planning")

# Test 8: Create and retrieve articles (unique slug per run)
test_slug = f"test-hsr-layout-guide-{_uid}"
art_id = create_article(
    title="Test Article: HSR Layout Guide",
    slug=test_slug,
    cluster_id=cl_id,
    article_type="hub",
    target_keywords=["HSR Layout", "Bangalore"],
    outline=["H2: Overview", "H2: Property Market"],
)
check("Create article", art_id is not None and art_id.startswith("art-"))

article = get_article(art_id)
check("Retrieve article", article is not None)
check("Article title matches", article["title"] == "Test Article: HSR Layout Guide")
check("Article type is hub", article["article_type"] == "hub")
check("Article slug correct", article["slug"] == test_slug)

# Test 9: Get articles by cluster
cluster_articles = get_articles_by_cluster(cl_id)
check("Get articles by cluster", len(cluster_articles) >= 1)

# Test 10: Article by slug
by_slug = get_article_by_slug(test_slug)
check("Get article by slug", by_slug is not None)

# Test 11: Article history
add_article_history(art_id, "test_stage", "Test change", "Sample content here")
updated_art = get_article(art_id)
history = json.loads(updated_art.get("history", "[]"))
check("Article history appended", len(history) == 1)
check("History stage correct", history[0]["stage"] == "test_stage")

# Test 12: Agent runs
run_id = start_agent_run("test_agent", cluster_id=cl_id, article_id=art_id, input_summary="Test")
check("Start agent run", run_id is not None)

complete_agent_run(run_id, output_summary="Test output", tokens_in=100, tokens_out=50, cost_usd=0.001)
runs = get_agent_runs(agent_name="test_agent")
check("Complete and retrieve agent run", len(runs) >= 1)
check("Agent run status completed", runs[0]["status"] == "completed")
check("Agent run cost tracked", runs[0]["cost_usd"] == 0.001)

# Test 13: Failed agent run
run_id2 = start_agent_run("test_agent_fail")
fail_agent_run(run_id2, "Test error message")
failed_runs = get_agent_runs(agent_name="test_agent_fail")
check("Failed agent run tracked", failed_runs[0]["status"] == "failed")
check("Error log recorded", failed_runs[0]["error_log"] == "Test error message")

# Test 14: Verification queue
vq_id = add_to_verification_queue(
    fact_id=fact_id, claim_text="Test claim", issue_type="implausible",
    suggested_correction="Should be ₹30,000"
)
check("Add to verification queue", vq_id is not None)

pending = get_pending_verifications()
check("Get pending verifications", len(pending) >= 1)

resolve_verification(vq_id)
pending_after = get_pending_verifications()
check("Resolve verification", len(pending_after) < len(pending))

# Test 15: API cache
cache_set("test_key", {"result": "cached_value"}, ttl_days=1)
cached = cache_get("test_key")
check("Cache set and get", cached is not None)
check("Cache value correct", cached.get("result") == "cached_value")

# Test 16: Stats
stats = get_stats()
check("Get stats", stats is not None)
check("Stats has total_facts", stats["total_facts"] >= 1)
check("Stats has total_articles", stats["total_articles"] >= 1)
check("Stats has total_cost", "total_cost_usd" in stats)

# Test 17: Update cluster
update_cluster(cl_id, status="writing", name="Updated Cluster")
updated_cl = get_cluster(cl_id)
check("Update cluster status", updated_cl["status"] == "writing")
check("Update cluster name", updated_cl["name"] == "Updated Cluster")

# Test 18: List clusters
all_clusters = list_clusters()
check("List all clusters", len(all_clusters) >= 1)
writing_clusters = list_clusters(status="writing")
check("List clusters by status", len(writing_clusters) >= 1)

# ─── CLEANUP ───
# Order: children first, then parents (respects foreign keys)
conn = get_conn()
conn.execute("DELETE FROM agent_runs WHERE agent_name LIKE 'test_%'")
conn.execute("DELETE FROM verification_queue WHERE claim_text = 'Test claim'")
conn.execute("DELETE FROM articles WHERE id = ?", (art_id,))
conn.execute("DELETE FROM clusters WHERE id = ?", (cl_id,))
conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
conn.execute("DELETE FROM sources WHERE id = ?", (src_id,))
conn.execute("DELETE FROM api_cache WHERE cache_key = 'test_key'")
conn.commit()
conn.close()
check("Test data cleaned up", True)

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ SQLITE TEST FAILED")
    sys.exit(1)
else:
    print("✅ SQLITE TEST PASSED")