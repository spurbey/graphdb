"""
amo_dump_viz.py — Convert amo_nodes.json + amo_edges.json into graph_viz format
for tools/harness_graph_viz.html

Usage:
    cd graphdb
    python amo_dump_viz.py
    # Then open tools/harness_graph_viz.html -> Load JSON -> amo_viz.json
"""

import json

with open("sandbox/amo_nodes.json", encoding="utf-8") as f:
    nodes_data = json.load(f)
with open("sandbox/amo_edges.json", encoding="utf-8") as f:
    edges_data = json.load(f)

# ── Build viz nodes ───────────────────────────────────────────────────────────
viz_nodes = []
for n in nodes_data:
    # map label based on status
    kind = "FunctionIdentity" if n["status"] == "active" else "SupersededFunction"
    label = n["name"]
    summary = n.get("text_summary", "")[:300]
    file_short = n["file"].split("/")[-1]

    viz_nodes.append({
        "id":      n["id"],
        "kind":    kind,
        "label":   f"{label} ({file_short})"[:60],
        "summary": summary,
        "status":  n["status"],
        "metadata": {
            "file":          n["file"],
            "name":          n["name"],
            "version_count": n.get("version_count", 1),
            "has_embedding": len(n.get("embedding", [])) > 0,
        },
    })

# ── Build viz edges (sample — cap CO_CHANGE to keep viz fast) ─────────────────
# Full 494k edges will freeze D3. Strategy:
#   - All CALLS edges (4440)
#   - CO_CHANGE edges with count >= 5 only
#   - All IMPORTS edges capped at 2000

calls_edges    = [e for e in edges_data if e["type"] == "CALLS"]
cochange_edges = sorted(
    [e for e in edges_data if e["type"] == "CO_CHANGE"],
    key=lambda e: e["co_change_count"], reverse=True
)[:1000]   # top 1000 strongest co-changes only
imports_edges  = [e for e in edges_data if e["type"] == "IMPORTS"][:500]

sampled_edges = calls_edges + cochange_edges + imports_edges

viz_edges = [
    {
        "source": e["source"],
        "target": e["target"],
        "kind":   e["type"],
    }
    for e in sampled_edges
]

out = {"nodes": viz_nodes, "edges": viz_edges}

with open("sandbox/amo_viz.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

print(f"Written amo_viz.json")
print(f"  {len(viz_nodes)} nodes")
print(f"  {len(viz_edges)} edges ({len(calls_edges)} CALLS + {len(cochange_edges)} CO_CHANGE>=5 + {len(imports_edges)} IMPORTS)")
print()
print("Open: tools/harness_graph_viz.html -> Load JSON -> amo_viz.json")
