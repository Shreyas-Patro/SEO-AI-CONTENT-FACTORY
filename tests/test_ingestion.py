"""Test: Research ingestion pipeline — extract, chunk, store, verify."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest.extract import extract_text
from ingest.chunk import chunk_text

print("=== TEST: Research Ingestion Pipeline ===")
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

# Test 1: Extract from Markdown
md_path = "research_docs/sample_hsr_layout.md"
if os.path.exists(md_path):
    text = extract_text(md_path)
    check("Extract text from .md", len(text) > 100, f"— Got {len(text)} chars")
    check("Extracted text has content", "HSR Layout" in text)
else:
    check("Sample research doc exists", False, f"— Create {md_path} first (Step 1.10)")

# Test 2: Chunking
chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
check("Chunking produces multiple chunks", len(chunks) >= 3, f"— Got {len(chunks)}")
check("Chunks are reasonable size", all(len(c) <= 700 for c in chunks),
      f"— Max chunk: {max(len(c) for c in chunks)} chars")
check("Chunks overlap (share content)", any(
    chunks[i][-30:] in chunks[i+1][:80] or chunks[i+1][:30] in chunks[i][-80:]
    for i in range(len(chunks)-1)
) if len(chunks) > 1 else True)

# Test 3: Full pipeline (this makes API calls — costs ~$0.01)
print("\n  Running full ingestion pipeline (makes API calls)...")
from ingest.pipeline import ingest_research_doc
summary = ingest_research_doc(
    md_path,
    source_url="https://test-source.com",
    source_title="Test HSR Layout Research"
)
check("Ingestion returns summary", summary is not None)
check("Facts extracted", summary.get("facts_extracted", 0) >= 3,
      f"— Got {summary.get('facts_extracted', 0)}")
check("Facts stored", summary.get("facts_stored", 0) >= 3,
      f"— Got {summary.get('facts_stored', 0)}")
check("Cost tracked", summary.get("cost_usd", 0) > 0)

# Test 4: Verify facts are searchable in ChromaDB
from db.chroma_ops import search_facts
results = search_facts("HSR Layout rent apartment", top_k=5)
check("Ingested facts are searchable", len(results) >= 1)

# Test 5: Verify facts are in SQLite
from db.sqlite_ops import get_facts
facts = get_facts(location="HSR Layout")
check("Facts stored in SQLite", len(facts) >= 1)

# Test 6: Verify graph was updated
from db.graph_ops import load_graph, get_nodes_by_type
G = load_graph()
fact_nodes = get_nodes_by_type(G, "fact")
check("Facts added to knowledge graph", len(fact_nodes) >= 1)

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ INGESTION TEST FAILED")
    sys.exit(1)
else:
    print("✅ INGESTION TEST PASSED")