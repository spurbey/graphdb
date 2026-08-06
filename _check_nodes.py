import json

# Check what repo the amo_nodes.json actually contains
with open('sandbox/amo_nodes.json') as f:
    nodes = json.load(f)

print(f"amo_nodes.json: {len(nodes)} nodes")
prefixes = {}
for n in nodes[:2000]:
    nid = n.get('id', '')
    prefix = nid.split('_')[0] if nid else '?'
    prefixes[prefix] = prefixes.get(prefix, 0) + 1
print(f"Sample ID prefixes: {dict(list(prefixes.items())[:5])}")

if nodes:
    print(f"First id: {nodes[0].get('id')}")
    print(f"Last id: {nodes[-1].get('id')}")

with open('sandbox/amo_edges.json') as f:
    edges = json.load(f)
print(f"\namo_edges.json: {len(edges)} edges")
if edges:
    print(f"First edge: {json.dumps(edges[0], indent=2)[:400]}")
