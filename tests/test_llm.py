"""Test: LLM caller — connection, retries, caching, JSON parsing."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import call_llm, call_llm_json

print("=== TEST: LLM Caller ===")
print("  (This test makes real API calls — costs ~$0.002)")
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

# Test 1: Basic call (Haiku — cheapest)
# IMPORTANT: use_cache=True so this response gets stored for Test 2
try:
    result = call_llm(
        "Respond with exactly: CANVAS_HOMES_TEST_OK",
        model_role="bulk",
        max_tokens=50,
        use_cache=True,  # Must be True — Test 2 reads from this cache
    )
    check("Basic LLM call succeeds", result is not None)
    check("Response has text", len(result["text"]) > 0)
    check("Response contains expected text", "CANVAS_HOMES_TEST_OK" in result["text"])
    check("Token count returned", result["tokens_in"] > 0 and result["tokens_out"] > 0)
    check("Cost calculated", result["cost_usd"] > 0)
    check("Model name returned", "haiku" in result["model"])
    # First call might be cached from a previous test run — that's fine
    # We just need to confirm the field exists and is a bool
    check("'cached' field is boolean", isinstance(result["cached"], bool))
except Exception as e:
    check("Basic LLM call", False, f"— {e}")
    print("\n⚠️  LLM connection failed. Check your API key in config.yaml.")
    sys.exit(1)

# Test 2: Caching works — same prompt should return cached response
result2 = call_llm(
    "Respond with exactly: CANVAS_HOMES_TEST_OK",
    model_role="bulk",
    max_tokens=50,
    use_cache=True,
)
check("Cached response returned", result2["cached"] == True,
      f"— Got cached={result2.get('cached')}. If this is the very first run, "
      f"the first call may have been cached=False and stored it, "
      f"so the second call should be True.")
check("Cached text matches", result2["text"] == result["text"])

# Test 3: use_cache=False bypasses cache
result_nocache = call_llm(
    "Respond with exactly: CANVAS_HOMES_TEST_OK",
    model_role="bulk",
    max_tokens=50,
    use_cache=False,
)
check("use_cache=False makes a fresh call", result_nocache["cached"] == False)

# Test 4: JSON response parsing
result3 = call_llm_json(
    'Respond with ONLY this JSON, no other text: {"name": "Canvas Homes", "city": "Bangalore", "score": 42}',
    model_role="bulk",
    max_tokens=100,
)
check("JSON parsing succeeds", "parsed" in result3)
check("Parsed JSON has correct fields", result3["parsed"].get("city") == "Bangalore")
check("Parsed JSON number correct", result3["parsed"].get("score") == 42)

# Test 5: System prompt works
result4 = call_llm(
    "What company are you writing for?",
    system="You are a writer for Canvas Homes. Always mention 'Canvas Homes' in your response.",
    model_role="bulk",
    max_tokens=100,
    use_cache=False,
)
check("System prompt followed", "Canvas Homes" in result4["text"] or "canvas homes" in result4["text"].lower())

print(f"\nResults: {passed} passed, {failed} failed")
total_cost = sum(r.get("cost_usd", 0) for r in [result, result_nocache, result3, result4] if not r.get("cached"))
print(f"Total test cost: ~${total_cost:.4f}")
if failed > 0:
    print("❌ LLM TEST FAILED")
    sys.exit(1)
else:
    print("✅ LLM TEST PASSED")