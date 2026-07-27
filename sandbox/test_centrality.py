import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pipeline_api

pipeline_api.initialize(source="files")
p = pipeline_api._pipeline

# 1. Betweenness Centrality
print("Computing Betweenness Centrality (top 20)...")
pipeline_api._compute_betweenness()
betweenness = pipeline_api._betweenness
top_b = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:20]

# 2. Reverse PageRank (Impact Rank)
print("Computing Reverse PageRank (Impact Rank)...")
# Build reversed CALLS-only subgraph
calls_ids = [e.index for e in p.G.es if e["type"] == "CALLS"]
# To do reverse pagerank, we reverse the direction of edges in the calculation.
# However, pagerank in igraph has a `directed=True` param. If we just reverse the graph:
calls_sub = p.G.subgraph_edges(calls_ids, delete_vertices=False)
edges = [(e.target, e.source) for e in calls_sub.es]
import igraph
rev_G = igraph.Graph(n=p.G.vcount(), edges=edges, directed=True)

pr = rev_G.pagerank(directed=True)

cnt = sum(1 for v in p.G.vs if v.attributes().get("status") == "active" and v.attributes().get("name"))
print(f"DEBUG: Found {cnt} active named nodes")
print(f"DEBUG: Max PR score: {max(pr)}")

impact_rank = {}
for i, v in enumerate(p.G.vs):
    if v.attributes().get("status") == "active" and v.attributes().get("name"):
        impact_rank[v["name"]] = pr[i]

top_pr = sorted(impact_rank.items(), key=lambda x: x[1], reverse=True)[:20]

print("\n--- Top 20 Betweenness ---")
for i, (fid, score) in enumerate(top_b):
    print(f"{i+1:2d}. {fid.split('::')[-1]} ({score:.4f})")

print("\n--- Top 20 Impact Rank ---")
for i, (fid, score) in enumerate(top_pr):
    print(f"{i+1:2d}. {fid.split('::')[-1]} ({score:.4f})")
