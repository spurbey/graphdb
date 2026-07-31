"""
Show PPR vs pure vector on ONE function — raw, no fluff.
"""
import sys; sys.path.insert(0, '.')
from pipeline_api import initialize; initialize()
from pipeline_api import _pipeline as p
import numpy as np
from sandbox.igraph_sandbox import cosine_sim, _is_candidate

# Pick one function with embedding
active = [i for i, n in enumerate(p.nodes_data)
          if n.get("status")=="active" and n.get("embedding")
          and any(abs(v)>1e-12 for v in n["embedding"])]
idx = active[len(active)//2]
v = p.G.vs[idx]
q = np.array(v["embedding"], dtype=np.float32)

print(f"TARGET: {v['name']} ({v['file'].split('/')[-1]})")
print()

# --- VECTOR top-10 ---
sims = [(i, cosine_sim(q, np.array(p.G.vs[i]["embedding"], dtype=np.float32)))
         for i in range(p.G.vcount()) if _is_candidate(p.G.vs[i])]
sims.sort(key=lambda x: x[1], reverse=True)

print("PURE VECTOR TOP-10:")
for rank, (i, s) in enumerate(sims[:10]):
    vi = p.G.vs[i]
    print(f"  {rank+1}. [{s:.4f}] {vi['name']:35s} {vi['file'].split('/')[-1]:25s}")

# --- PPR on CALLS graph ---
seed_idx = [i for i, _ in sims[:20]]
reset = np.zeros(p.G.vcount())
for i, s in sims[:20]:
    reset[i] = s
reset /= reset.sum()

calls_e = [e.index for e in p.G.es if e["type"] == "CALLS"]
calls_g = p.G.subgraph_edges(calls_e, delete_vertices=False)
ppr = calls_g.personalized_pagerank(vertices=None, damping=0.85,
                                     directed=True, weights=None,
                                     reset=reset.tolist())
ppr_ranked = sorted(enumerate(ppr), key=lambda x: x[1], reverse=True)
ppr_ranked = [(i, s) for i, s in ppr_ranked if _is_candidate(p.G.vs[i])][:10]

print()
print("PPR-ON-CALLS TOP-10 (seeded on same vector top-20):")
for rank, (i, s) in enumerate(ppr_ranked):
    vi = p.G.vs[i]
    vec = sims[i][1] if i < len(sims) else 0
    deg = calls_g.degree(i, mode="in")
    print(f"  {rank+1}. [ppr={s:.6f} vec={vec:.4f} indeg={deg:3d}] {vi['name']:35s} {vi['file'].split('/')[-1]:25s}")

# What did PPR drag in that vector didn't?
vec_set = set(i for i,_ in sims[:10])
ppr_set = set(i for i,_ in ppr_ranked)
new = ppr_set - vec_set
print()
print(f"PPR ADDED {len(new)} items NOT in vector top-10:")
for i in sorted(new, key=lambda x: ppr[x], reverse=True):
    vi = p.G.vs[i]
    indeg = calls_g.degree(i, mode="in")
    outdeg = calls_g.degree(i, mode="out")
    # Find which seeds call this
    called_by = []
    for e in calls_g.es:
        if e.target==i and calls_g.vs[e.source].index in seed_idx:
            called_by.append(calls_g.vs[e.source]["name"])
    print(f"  {vi['name']:35s} indeg={indeg} outdeg={outdeg} called_by_seeds={called_by[:4]}")
