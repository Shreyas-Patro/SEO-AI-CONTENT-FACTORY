"""Test: NetworkX knowledge graph operations."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use a test graph file so we don't pollute the real one
os.environ["CANVAS_TEST_GRAPH"] = "1"
import db.graph_ops as graph_ops
graph_ops.GRAPH_PATH = "data/test_graph.graphml"

from db.graph_ops import *

print("=== TEST: Knowledge Graph (NetworkX) ===")
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

# Test 1: Create/load empty graph
G = load_graph()
check("Load graph", G is not None)
check("Graph is directed", G.is_directed())

# Test 2: Add nodes
add_location(G, "HSR Layout")
add_location(G, "Koramangala")
add_topic(G, "rental market")
add_topic(G, "lifestyle")
check("Add location nodes", "loc:hsr-layout" in G.nodes)
check("Add topic nodes", "topic:rental-market" in G.nodes)

# Test 3: Add article node
add_article_node(G, "art-001", "HSR Layout Guide", "hsr-layout-guide", "hub")
check("Add article node", "article:art-001" in G.nodes)

# Test 4: Add fact node
add_fact_node(G, "fact-001", "Average rent 2BHK HSR ₹28,000")
check("Add fact node", "fact:fact-001" in G.nodes)

# Test 5: Add edges
link_article_to_location(G, "art-001", "HSR Layout")
link_article_to_topic(G, "art-001", "rental market")
link_article_cites_fact(G, "art-001", "fact-001")
check("Article → Location edge", G.has_edge("article:art-001", "loc:hsr-layout"))
check("Article → Topic edge", G.has_edge("article:art-001", "topic:rental-market"))
check("Article → Fact edge", G.has_edge("article:art-001", "fact:fact-001"))

# Test 6: Add inter-article link
add_article_node(G, "art-002", "HSR Rent Prices", "hsr-rent-prices", "spoke")
link_articles(G, "art-001", "art-002", anchor_text="rent prices in HSR Layout")
edge_data = G.edges["article:art-001", "article:art-002"]
check("Article → Article edge", G.has_edge("article:art-001", "article:art-002"))
check("Edge has anchor_text", edge_data.get("anchor_text") == "rent prices in HSR Layout")

# Test 7: Get node data
node = get_node(G, "article:art-001")
check("Get node data", node is not None)
check("Node has label", node.get("label") == "HSR Layout Guide")
check("Node has type", node.get("node_type") == "article")

# Test 8: Get neighbors
neighbors = get_neighbors(G, "article:art-001")
check("Get neighbors", len(neighbors) >= 3,
      f"— Expected ≥3, got {len(neighbors)}")

neighbors_by_type = get_neighbors(G, "article:art-001", edge_type="LOCATED_IN")
check("Get neighbors filtered by edge type", len(neighbors_by_type) >= 1)

# Test 9: Get subgraph
sub = get_subgraph(G, "article:art-001", depth=1)
check("Get subgraph depth 1", sub.number_of_nodes() >= 4,
      f"— Expected ≥4 nodes, got {sub.number_of_nodes()}")

sub2 = get_subgraph(G, "article:art-001", depth=2)
check("Get subgraph depth 2", sub2.number_of_nodes() >= sub.number_of_nodes())

# Test 10: Get nodes by type
locations = get_nodes_by_type(G, "location")
check("Get nodes by type (location)", len(locations) == 2)
articles = get_nodes_by_type(G, "article")
check("Get nodes by type (article)", len(articles) == 2)

# Test 11: Graph stats
stats = graph_stats(G)
check("Graph stats", stats["total_nodes"] >= 6)
check("Graph has edges", stats["total_edges"] >= 4)

# Test 12: Save and reload
save_graph(G)
check("Graph saved", os.path.exists("data/test_graph.graphml"))

G2 = load_graph()
check("Graph reloaded", G2.number_of_nodes() == G.number_of_nodes())
check("Reloaded graph preserves edges", G2.number_of_edges() == G.number_of_edges())

# Test 13: Node that doesn't exist
none_node = get_node(G, "nonexistent")
check("Nonexistent node returns None", none_node is None)

none_neighbors = get_neighbors(G, "nonexistent")
check("Nonexistent neighbors returns empty", len(none_neighbors) == 0)

none_sub = get_subgraph(G, "nonexistent")
check("Nonexistent subgraph returns empty", none_sub.number_of_nodes() == 0)

# Clean up
os.remove("data/test_graph.graphml")
check("Test graph cleaned up", not os.path.exists("data/test_graph.graphml"))

print(f"\nResults: {passed} passed, {failed} failed")
if failed > 0:
    print("❌ GRAPH TEST FAILED")
    sys.exit(1)
else:
    print("✅ GRAPH TEST PASSED")