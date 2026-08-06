import json, subprocess, os, tempfile

shas = ["5f6cda8", "4fd86ef", "0051850", "bcb657f"]

for sha in shas:
    content = subprocess.run(
        ["git", "show", f"{sha}:graph_payload.json"],
        capture_output=True, text=True, cwd=os.getcwd()
    ).stdout
    if not content.strip():
        print(f"{sha}: empty/none")
        continue
    try:
        d = json.loads(content)
        print(f"{sha}: {len(d.get('nodes', []))} nodes, {len(d.get('edges', []))} edges")
        if d.get('nodes'):
            print(f"    sample node id: {d['nodes'][0].get('id', d['nodes'][0].get('node_id','?'))}")
    except Exception as e:
        print(f"{sha}: parse error {e}")

# Current working file
with open('graph_payload.json') as f:
    cur = json.load(f)
print(f"\nCURRENT (working tree): {len(cur.get('nodes', []))} nodes, {len(cur.get('edges', []))} edges")
