import urllib.request, json

def call(tool, params):
    payload = json.dumps({"name": tool, "parameters": params}).encode()
    req = urllib.request.Request("http://127.0.0.1:7700/call", data=payload,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["result"]

queries = [
    "user registration and account creation pipeline",
    "authenticate password login",
    "delete remove account",
]
for q in queries:
    r = call("search_code_semantics", {"prompt": q, "k": 3})
    print("Query:", q)
    if isinstance(r, list) and r and "error" in r[0]:
        print("  ERROR:", r[0]["error"])
    else:
        for item in r:
            fid = item.get("function_id", "")
            summary = item.get("ai_summary", "")[:65]
            print(" ", fid, "-", summary)
    print()
