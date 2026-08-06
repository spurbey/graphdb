import json, sys, os

# Check turbovec code index — what's actually in it
print("=== turbovec_code.json (first 20 entries) ===")
try:
    with open('.turbovec_code.json') as f:
        data = json.load(f)
    if isinstance(data, dict):
        print(f"Dict with {len(data)} keys")
        for k, v in list(data.items())[:5]:
            print(f"  {k}: {str(v)[:80]}")
    elif isinstance(data, list):
        print(f"List with {len(data)} entries")
        for e in data[:5]:
            print(f"  {str(e)[:120]}")
except Exception as e:
    print(f"Error: {e}")

# Check the igraph nodes_data that the MCP server would serve
print()
print("=== What does pipeline load for dograh? ===")
try:
    sys.path.insert(0, '.')
    from pipeline_api import initialize
    initialize()
    from pipeline_api import _pipeline as p
    print(f"Graph loaded: {p.G.vcount()} vertices, {p.G.ecount()} edges")
    print(f"nodes_data: {len(p.nodes_data)}")
    active = [n for n in p.nodes_data if n.get('status')=='active']
    print(f"active: {len(active)}")
    with_emb = [n for n in active if n.get('embedding') and any(abs(v)>1e-12 for v in n['embedding'])]
    print(f"active with embedding: {len(with_emb)}")
    if p.nodes_data:
        print(f"Sample id: {p.nodes_data[0]['id']}")
except Exception as e:
    print(f"Init error: {e}")
