"""
Rebuild graph_viz.json for the visualizer by merging:
  - The structural graph already written by scalable_ingest.py (graph_payload.json)
  - Semantic nodes (ChangeNode, Concept, Rationale) read live from HelixDB

Open tools/harness_graph_viz.html -> Load JSON -> select graph_viz.json
"""
import json
from helixdb import Client, g, read_batch, define_params, param, Predicate

HELIX_URL = "http://127.0.0.1:6969"

SEMANTIC_KINDS = ["ChangeNode", "Concept", "Rationale"]

SEMANTIC_EDGE_KINDS = ["HAS_CHANGE", "HAS_SEMANTIC", "DESCRIBES"]


def _label(kind, r):
    if kind == "ChangeNode": return "ChangeNode: " + r.get("commit", "")
    return r.get("text", "")[:60] or r.get("node_id", "")


def _summary(kind, r):
    if kind == "ChangeNode": return r.get("summary", "")[:400]
    return r.get("text", "")


def dump():
    # ── load structural graph from ingest artefact ─────────────────────────
    with open("graph_payload.json", encoding="utf-8") as f:
        payload = json.load(f)

    viz_nodes = []
    for n in payload["nodes"]:
        kind  = n["type"]
        label = n.get("name") or (n.get("msg", "").splitlines() or [""])[0] or n.get("file") or n["id"]
        summary = n.get("reasoning") or n.get("code", "")[:200]
        viz_nodes.append({
            "id": n["id"], "kind": kind, "label": label[:60],
            "summary": summary[:300], "status": "active",
            "metadata": {k: v for k, v in n.items() if k not in ("type", "id")},
        })
    viz_edges = [{"source": e["from"], "target": e["to"], "kind": e["label"]}
                 for e in payload["edges"]]

    existing_ids = {n["id"] for n in viz_nodes}

    # ── append semantic nodes from HelixDB ─────────────────────────────────
    c = Client(HELIX_URL)

    for kind in SEMANTIC_KINDS:
        batch = read_batch().var_as("n", g().n_with_label(kind).value_map()).returning(["n"])
        rows = c.query().dynamic(batch.to_dynamic_request()).send().get("n", {}).get("properties", [])
        for r in rows:
            nid = r.get("node_id", "")
            if not nid or nid in existing_ids:
                continue
            viz_nodes.append({
                "id": nid, "kind": kind,
                "label":   _label(kind, r),
                "summary": _summary(kind, r),
                "status":  "semantic",
                "metadata": {k: v for k, v in r.items() if not k.startswith("$")},
            })
            existing_ids.add(nid)

    # ── append semantic edges from HelixDB ─────────────────────────────────
    seen = {(e["source"], e["target"], e["kind"]) for e in viz_edges}

    # For each semantic edge type, get all (src_node_id, tgt_node_id) pairs
    # by reading the source nodes and traversing outgoing edges one-by-one.
    for label, src_kind in [("HAS_CHANGE", "Commit"), ("HAS_SEMANTIC", "Commit"),
                             ("DESCRIBES", "ChangeNode")]:
        # get all source nodes
        src_batch = read_batch().var_as("n", g().n_with_label(src_kind).value_map()).returning(["n"])
        src_rows = c.query().dynamic(src_batch.to_dynamic_request()).send().get("n", {}).get("properties", [])
        for src_r in src_rows:
            src_nid = src_r.get("node_id", "")
            if not src_nid or src_nid not in existing_ids:
                continue
            # traverse outgoing edges of this label from this specific node
            p = define_params({"sid": param.string()})
            tgt_batch = (
                read_batch()
                .var_as("tgts",
                    g().n_with_label(src_kind)
                       .where(Predicate.eq_param("node_id", "sid"))
                       .out_e(label).out_n().value_map()
                )
                .returning(["tgts"])
            )
            try:
                result = c.query().dynamic(tgt_batch.to_dynamic_request(p, {"sid": src_nid})).send()
                tgt_rows = result.get("tgts", {}).get("properties", [])
                for tgt_r in tgt_rows:
                    tgt_nid = tgt_r.get("node_id", "")
                    key = (src_nid, tgt_nid, label)
                    if tgt_nid and tgt_nid in existing_ids and key not in seen:
                        viz_edges.append({"source": src_nid, "target": tgt_nid, "kind": label})
                        seen.add(key)
            except Exception:
                pass

    out = {"nodes": viz_nodes, "edges": viz_edges}
    with open("graph_viz.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"graph_viz.json: {len(viz_nodes)} nodes, {len(viz_edges)} edges")
    print("Open tools/harness_graph_viz.html -> Load JSON -> graph_viz.json")


if __name__ == "__main__":
    dump()
