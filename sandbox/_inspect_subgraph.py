import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sg = json.loads((ROOT / "sandbox" / "out" / "subgraph_memory_ingestion_pipeline_hook_processin.json").read_text(encoding="utf-8"))

print("query:", sg["query"])
print("consumer_mode:", sg["consumer_mode"])
print(f"nodes: {len(sg['nodes'])}  edges: {len(sg['edges'])}")
print()
print("NODES:")
for n in sg["nodes"]:
    fname = n["file"].split("/")[-1]
    ppr = n["ppr_score"]
    vec = n["vector_score"]
    print(f"  {n['name']} [{fname}]  ppr={ppr:.5f}  vec={vec:.3f}  community={n['community_id']}")
    if n["summary"]:
        print(f"    summary: {n['summary'][:80]}")

print()
print("EDGES:")
for e in sg["edges"]:
    src = e["source"].split("::")[-1]
    tgt = e["target"].split("::")[-1]
    etype = e["type"]
    cat = e["co_change_category"]
    print(f"  {src} --[{etype}{(' '+cat) if cat else ''}]--> {tgt}")
