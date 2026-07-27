import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pipeline_api

pipeline_api.initialize(source="files")
p = pipeline_api._pipeline

# Find CO_CHANGE edges
co_edges = [e.index for e in p.G.es if e["type"] == "CO_CHANGE"]
print(f"Total CO_CHANGE edges: {len(co_edges)}")

if len(co_edges) == 0:
    print("No CO_CHANGE edges in the graph. PPR on CO_CHANGE will not work on this dataset.")
    sys.exit(0)

# Hardcoded seeds to avoid dimension mismatch (amo_nodes.json has 2048-dim vectors, _embed has 384)
top_3_idx = []
for i, v in enumerate(p.G.vs):
    if v.attributes().get("status") == "active" and v.attributes().get("name") in ("search", "process_event", "graph_search"):
        top_3_idx.append(i)
        if len(top_3_idx) == 3:
            break

print(f"Top 3 Seeds:")
for i in top_3_idx:
    print(f"- {p.G.vs[i]['name']}")

# Run PPR explicitly on CO_CHANGE graph
co_sub = p.G.subgraph_edges(co_edges, delete_vertices=False)
reset = [0.0] * p.G.vcount()
if top_3_idx:
    for idx in top_3_idx:
        reset[idx] = 1.0 / len(top_3_idx)
else:
    print("No seeds found.")
    sys.exit(0)

ppr = co_sub.personalized_pagerank(
    vertices=None, damping=0.85, directed=False, reset=reset
)

ppr_scores = []
for i, v in enumerate(p.G.vs):
    if v.attributes().get("status") == "active" and ppr[i] > 0 and i not in top_3_idx:
        ppr_scores.append((ppr[i], v.attributes().get("name", "Unknown")))

ppr_scores.sort(key=lambda x: x[0], reverse=True)

print("\n--- Top 10 Functions Pulled in via CO_CHANGE PPR ---")
for score, name in ppr_scores[:10]:
    print(f"- {name} (PPR: {score:.6f})")
