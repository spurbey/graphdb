import json
from collections import Counter

with open('graph_payload.json') as f:
    data = json.load(f)

nodes = data.get('nodes', [])
edges = data.get('edges', [])
print(f"Total nodes: {len(nodes)}")
print(f"Total edges: {len(edges)}")

types = Counter(n.get('type', '?') for n in nodes)
print(f"Node types: {dict(types)}")

labels = Counter(e.get('label', '?') for e in edges)
print(f"Edge labels: {dict(labels)}")

if nodes:
    # check sample node
    n = nodes[0]
    print(f"Sample node keys: {list(n.keys())}")
    print(f"Sample: {json.dumps(n, indent=2)[:500]}")
else:
    print("NO NODES — nodes array is empty")
