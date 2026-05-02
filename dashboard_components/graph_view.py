"""
Streamlit dashboard component for the knowledge graph.

Drop into dashboard.py with:
    from dashboard_components.graph_view import render_graph_view
    render_graph_view()
"""

import os
import streamlit as st
import streamlit.components.v1 as components
import tempfile
from pathlib import Path

from db.graph_ops import load_graph, graph_stats, get_nodes_by_type
from viz.graph_viewer import export_to_html


def render_graph_view():
    st.markdown("###  Knowledge Graph")

    G = load_graph()
    stats = graph_stats(G)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total nodes", stats["total_nodes"])
    c2.metric("Total edges", stats["total_edges"])

    type_counts = {}
    for n, data in G.nodes(data=True):
        t = data.get("node_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    c3.metric("Locations", type_counts.get("location", 0))
    c4.metric("Facts", type_counts.get("fact", 0))

    if stats["total_nodes"] == 0:
        st.info("Graph is empty — ingest a research document to populate.")
        return

    # ─── Interactive viewer ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### Interactive view")

    fc1, fc2 = st.columns([2, 1])
    filter_type = fc1.selectbox(
        "Filter by node type",
        ["all"] + sorted(type_counts.keys()),
        key="graph_filter",
    )
    max_nodes = fc2.slider("Max nodes to render", 50, 1000, 200, key="graph_max_nodes")

    if st.button("🎨 Render graph", key="graph_render_btn"):
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as tmp:
            tmp_path = tmp.name

        with st.spinner("Rendering..."):
            export_to_html(
                tmp_path,
                filter_node_type=None if filter_type == "all" else filter_type,
                max_nodes=max_nodes,
            )

        try:
            html_content = Path(tmp_path).read_text(encoding="utf-8")
            components.html(html_content, height=820, scrolling=False)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ─── Type breakdown ──────────────────────────────────────────────────
    st.markdown("#### Nodes by type")
    for ntype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        with st.expander(f"{ntype} — {count} nodes"):
            sample = get_nodes_by_type(G, ntype)[:20]
            for n in sample:
                st.caption(f"`{n['node_id']}` — {n.get('label', '—')[:80]}")
            if len(sample) >= 20:
                st.caption(f"... and {count - 20} more")