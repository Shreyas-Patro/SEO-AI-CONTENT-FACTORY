"""
Knowledge Graph — NetworkX-based entity relationship graph.
Nodes: locations, topics, articles, facts, keywords
Edges: COVERS, LOCATED_IN, CITES, LINKS_TO, RELATED_TO, TARGETS
"""

import networkx as nx
import json
import os
from config_loader import get_path

GRAPH_PATH = get_path("graph_file")


def load_graph():
    """Load graph from disk, or create empty one."""
    if os.path.exists(GRAPH_PATH):
        return nx.read_graphml(GRAPH_PATH)
    return nx.DiGraph()


def save_graph(G):
    """Persist graph to disk."""
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    nx.write_graphml(G, GRAPH_PATH)


def add_node(G, node_id, node_type, **attrs):
    """Add a node with type and attributes."""
    G.add_node(node_id, node_type=node_type, **attrs)
    save_graph(G)
    return G


def add_edge(G, source, target, edge_type, **attrs):
    """Add a directed edge between two nodes."""
    G.add_edge(source, target, edge_type=edge_type, **attrs)
    save_graph(G)
    return G


def get_node(G, node_id):
    """Get node data."""
    if node_id in G.nodes:
        return dict(G.nodes[node_id])
    return None


def get_neighbors(G, node_id, edge_type=None):
    """Get all nodes connected to a given node."""
    if node_id not in G.nodes:
        return []
    neighbors = []
    for _, target, data in G.out_edges(node_id, data=True):
        if edge_type is None or data.get("edge_type") == edge_type:
            neighbors.append({"node_id": target, **dict(G.nodes[target]), "edge": data})
    for source, _, data in G.in_edges(node_id, data=True):
        if edge_type is None or data.get("edge_type") == edge_type:
            neighbors.append({"node_id": source, **dict(G.nodes[source]), "edge": data})
    return neighbors


def get_subgraph(G, center_node, depth=2):
    """Get a subgraph centered on a node up to N hops."""
    if center_node not in G.nodes:
        return nx.DiGraph()
    nodes = {center_node}
    frontier = {center_node}
    for _ in range(depth):
        next_frontier = set()
        for node in frontier:
            next_frontier.update(G.successors(node))
            next_frontier.update(G.predecessors(node))
        nodes.update(next_frontier)
        frontier = next_frontier
    return G.subgraph(nodes).copy()


def get_nodes_by_type(G, node_type):
    """Get all nodes of a given type."""
    return [
        {"node_id": n, **dict(G.nodes[n])}
        for n in G.nodes
        if G.nodes[n].get("node_type") == node_type
    ]


def graph_stats(G):
    """Basic graph statistics."""
    return {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "node_types": dict(
            sorted(
                {G.nodes[n].get("node_type", "unknown"): 0 for n in G.nodes}.items()
            )
        ),
    }


# Convenience functions for common operations

def add_location(G, name):
    return add_node(G, f"loc:{name.lower().replace(' ', '-')}", "location", label=name)

def add_topic(G, name):
    return add_node(G, f"topic:{name.lower().replace(' ', '-')}", "topic", label=name)

def add_article_node(G, article_id, title, slug, article_type):
    return add_node(G, f"article:{article_id}", "article",
                    label=title, slug=slug, article_type=article_type)

def add_fact_node(G, fact_id, summary):
    return add_node(G, f"fact:{fact_id}", "fact", label=summary[:80])

def link_article_to_location(G, article_id, location_name):
    return add_edge(G, f"article:{article_id}",
                    f"loc:{location_name.lower().replace(' ', '-')}", "LOCATED_IN")

def link_article_to_topic(G, article_id, topic_name):
    return add_edge(G, f"article:{article_id}",
                    f"topic:{topic_name.lower().replace(' ', '-')}", "COVERS")

def link_article_cites_fact(G, article_id, fact_id):
    return add_edge(G, f"article:{article_id}", f"fact:{fact_id}", "CITES")

def link_articles(G, source_article_id, target_article_id, anchor_text=""):
    return add_edge(G, f"article:{source_article_id}", f"article:{target_article_id}",
                    "LINKS_TO", anchor_text=anchor_text)


if __name__ == "__main__":
    G = load_graph()

    # Test: Add some sample data
    add_location(G, "HSR Layout")
    add_location(G, "Koramangala")
    add_topic(G, "rental market")
    add_topic(G, "lifestyle")

    print(f"Graph stats: {graph_stats(G)}")
    print(f"Graph saved to {GRAPH_PATH}")