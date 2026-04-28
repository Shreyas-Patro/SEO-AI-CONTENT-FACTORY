"""
viz/graph_viewer.py — export graph to interactive HTML.
"""

from db.graph_ops import load_graph


def export_to_html(output_path, filter_node_type=None, max_nodes=200):
    """Export the knowledge graph as an interactive HTML file using vis.js."""
    G = load_graph()

    nodes_data = []
    edges_data = []

    colors = {
        "location": "#00d4ff", "topic": "#c8ff00", "article": "#ff6b6b",
        "fact": "#aa88ff", "keyword": "#5a9fff",
    }

    count = 0
    for node_id, data in G.nodes(data=True):
        ntype = data.get("node_type", "unknown")
        if filter_node_type and ntype != filter_node_type:
            continue
        if count >= max_nodes:
            break
        nodes_data.append({
            "id": node_id,
            "label": data.get("label", node_id)[:30],
            "color": colors.get(ntype, "#888"),
            "title": f"{ntype}: {data.get('label', node_id)}",
        })
        count += 1

    node_ids = {n["id"] for n in nodes_data}
    for src, tgt, data in G.edges(data=True):
        if src in node_ids and tgt in node_ids:
            edges_data.append({
                "from": src, "to": tgt,
                "label": data.get("edge_type", ""),
            })

    import json
    html = f"""<!DOCTYPE html>
<html><head>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>body{{margin:0;background:#0f0f1a}}#graph{{width:100%;height:800px}}</style>
</head><body>
<div id="graph"></div>
<script>
var nodes = new vis.DataSet({json.dumps(nodes_data)});
var edges = new vis.DataSet({json.dumps(edges_data)});
var container = document.getElementById('graph');
var data = {{nodes: nodes, edges: edges}};
var options = {{
    nodes: {{font: {{color: '#fff'}}, shape: 'dot', size: 12}},
    edges: {{color: '#444', font: {{color: '#888', size: 9}}, arrows: 'to'}},
    physics: {{solver: 'forceAtlas2Based', forceAtlas2Based: {{gravitationalConstant: -30}}}},
}};
new vis.Network(container, data, options);
</script></body></html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)