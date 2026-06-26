"""
Rebuild graph_viz.json for the visualizer from graph_payload.json.
Semantic data (ai_summary, ai_rationale) is already embedded in the structural nodes
by semantic_pass.py, so no separate HelixDB queries for semantic node kinds are needed.

Open tools/harness_graph_viz.html -> Load JSON -> select graph_viz.json
"""
import json

def dump():
    with open("graph_payload.json", encoding="utf-8") as f:
        payload = json.load(f)

    viz_nodes = []
    for n in payload["nodes"]:
        kind  = n["type"]
        label = (n.get("name")
                 or (n.get("msg", "").splitlines() or [""])[0]
                 or n.get("file")
                 or n["id"])
        # summary: prefer semantic enrichment, fall back to code snippet
        summary = (n.get("ai_rationale")
                   or n.get("ai_summary")
                   or n.get("code", "")[:200])
        viz_nodes.append({
            "id":       n["id"],
            "kind":     kind,
            "label":    label[:60],
            "summary":  summary[:400],
            "status":   "active",
            "metadata": {k: v for k, v in n.items() if k not in ("type", "id")},
        })

    viz_edges = [{"source": e["from"], "target": e["to"], "kind": e["label"]}
                 for e in payload["edges"]]

    out = {"nodes": viz_nodes, "edges": viz_edges}
    with open("graph_viz.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"graph_viz.json: {len(viz_nodes)} nodes, {len(viz_edges)} edges")
    print("Open tools/harness_graph_viz.html -> Load JSON -> graph_viz.json")


if __name__ == "__main__":
    dump()
