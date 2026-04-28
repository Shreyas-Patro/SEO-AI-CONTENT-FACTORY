"""
Standalone Knowledge Graph Visualizer.

Run independently:
    python -m viz.graph_viewer

OR launch interactive HTML:
    python -m viz.graph_viewer --html graph.html

OR view in dashboard via dashboard_components/graph_view.py
"""

import os
import sys
import argparse
from pathlib import Path

from db.graph_ops import load_graph, graph_stats, get_nodes_by_type


def export_to_html(output_file="graph.html", filter_node_type=None, max_nodes=500):
    """
    Render the graph to an interactive HTML page using pyvis.
    """
    try:
        from pyvis.network import Network
    except ImportError:
        print("Install pyvis first: pip install pyvis")
        return False

    G = load_graph()
    print(f"Loaded graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    if filter_node_type:
        keep = [n for n in G.nodes if G.nodes[n].get("node_type") == filter_node_type]
        keep = set(keep + list({m for n in keep for m in G.neighbors(n)}))
        G = G.subgraph(keep).copy()
        print(f"Filtered to {filter_node_type}: {G.number_of_nodes()} nodes")

    if G.number_of_nodes() > max_nodes:
        print(f"Graph has {G.number_of_nodes()} nodes — truncating to {max_nodes}")
        nodes_to_keep = list(G.nodes)[:max_nodes]
        G = G.subgraph(nodes_to_keep).copy()

    color_map = {
        "location": "#00d4ff",
        "topic": "#c8ff00",
        "article": "#5a9fff",
        "fact": "#aa88ff",
        "keyword": "#ffcc44",
    }

    net = Network(
        height="800px", width="100%",
        bgcolor="#0f0f1a", font_color="#aaa",
        notebook=False, directed=True,
    )

    for node, data in G.nodes(data=True):
        ntype = data.get("node_type", "unknown")
        label = data.get("label", node)[:50]
        net.add_node(
            node,
            label=label,
            color=color_map.get(ntype, "#888"),
            title=f"{ntype}: {data.get('label', '')}",
            size=20 if ntype in ("location", "topic") else 12,
        )

    for s, t, data in G.edges(data=True):
        net.add_edge(s, t, title=data.get("edge_type", ""), arrows="to")

    net.set_options("""
    {
      "physics": {"barnesHut": {"gravitationalConstant": -10000}},
      "interaction": {"hover": true, "navigationButtons": true}
    }
    """)
    net.write_html(output_file, notebook=False, open_browser=False)
    print(f"✓ Graph rendered to {output_file}")
    print(f"  Open in browser: file://{Path(output_file).absolute()}")
    return True


def print_summary():
    """CLI summary of the graph contents."""
    G = load_graph()
    stats = graph_stats(G)
    print("\n" + "=" * 50)
    print("KNOWLEDGE GRAPH SUMMARY")
    print("=" * 50)
    print(f"Total nodes: {stats['total_nodes']}")
    print(f"Total edges: {stats['total_edges']}")

    counts_by_type = {}
    for node, data in G.nodes(data=True):
        t = data.get("node_type", "unknown")
        counts_by_type[t] = counts_by_type.get(t, 0) + 1

    print("\nNodes by type:")
    for ntype, count in sorted(counts_by_type.items(), key=lambda x: -x[1]):
        print(f"  {ntype:15s} {count}")

    print("\nSample locations:")
    for loc in get_nodes_by_type(G, "location")[:10]:
        print(f"  - {loc.get('label')}")

    print("\nSample facts (first 5):")
    for fact in get_nodes_by_type(G, "fact")[:5]:
        print(f"  - {fact.get('label')[:80]}")


def main():
    parser = argparse.ArgumentParser(description="Knowledge Graph Viewer")
    parser.add_argument("--html", help="Export interactive HTML to this file")
    parser.add_argument("--filter", help="Filter to one node type (location, fact, etc.)")
    parser.add_argument("--max-nodes", type=int, default=500)
    args = parser.parse_args()

    if args.html:
        export_to_html(args.html, filter_node_type=args.filter, max_nodes=args.max_nodes)
    else:
        print_summary()


if __name__ == "__main__":
    main()