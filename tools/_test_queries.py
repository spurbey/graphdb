import urllib.request, json

def call(tool, params):
    payload = json.dumps({"name": tool, "parameters": params}).encode()
    req = urllib.request.Request("http://127.0.0.1:7700/call", data=payload,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())["result"]

# ── Query 1: Temporal Vulnerability Trace ─────────────────────────────────
print("=" * 60)
print("QUERY 1: Temporal Vulnerability Trace")
print("=" * 60)
r = call("get_temporal_vulnerability_trace", {
    "target_func": "create_user",
    "timestamp_iso": "2026-06-25T00:00:00+00:00"
})
if r:
    for item in r:
        print(f"STALE: {item['caller']} ({item['caller_node_id']})")
        print(f"  committed: {item['timestamp']}")
        print(f"  commit:    {item['commit_hash'][:12]}")
        print(f"  summary:   {item['ai_summary']}")
        print(f"  status:    {item['state_status']}")
else:
    print("No stale callers found.")

# ── Query 2: Time-Travel Diff ──────────────────────────────────────────────
print("\n" + "=" * 60)
print("QUERY 2: Time-Travel Diff on signup")
print("=" * 60)
r = call("get_code_time_travel_diff", {"state_node_id": "state_auth_service_signup_eef5695"})
cur  = r.get("current")
prev = r.get("previous")
if cur and prev:
    print(f"CURRENT  [{cur['commit']}] status={cur['status']}")
    print(f"  summary: {cur['ai_summary']}")
    print(f"  code:\n{cur['code']}\n")
    print(f"PREVIOUS [{prev['commit']}]")
    print(f"  summary: {prev.get('ai_summary','(none)')}")
    print(f"  code:\n{prev['code']}")
elif cur and not prev:
    print("FAIL: previous is null — PREVIOUS_VERSION edge missing")

# ── Query 3: Semantic Search ───────────────────────────────────────────────
print("\n" + "=" * 60)
print("QUERY 3: Semantic Search — 'user registration and account creation pipeline'")
print("=" * 60)
r = call("search_code_semantics", {
    "prompt": "user registration and account creation pipeline",
    "k": 5
})
if r:
    for item in r:
        print(f"  {item['function_id']}")
        print(f"    summary: {item['ai_summary']}")
else:
    print("No results.")

# ── Query 4: Full Automated Loop ───────────────────────────────────────────
print("\n" + "=" * 60)
print("QUERY 4: Full Automated Loop")
print("=" * 60)

# 4a. Trace
stale = call("get_temporal_vulnerability_trace", {
    "target_func": "create_user",
    "timestamp_iso": "2026-06-25T00:00:00+00:00"
})
print(f"Stale functions found: {len(stale)}")

for s in stale:
    fname = s["caller"]
    fid   = s["caller_node_id"]
    sid   = s["state_id"]
    print(f"\nTarget: {fname} — last stale commit {s['commit_hash'][:12]}")

    # 4b. Diff — find the active state id
    active_sid = sid.replace("623241a", "eef5695")
    diff = call("get_code_time_travel_diff", {"state_node_id": active_sid})
    cur  = diff.get("current", {})
    prev = diff.get("previous", {})
    print(f"  Current has 8-char check: {'8' in cur.get('code','')}")
    print(f"  Previous missing check:   {'8' not in prev.get('code','') if prev else 'n/a'}")

    # 4c. Only patch if current code doesn't already have the 8-char guard
    if "8" not in cur.get("code", ""):
        new_code = (
            'def signup(username, password):\n'
            '    """Register a new user. Enforces 8-char password minimum."""\n'
            '    try:\n'
            '        validate_username(username)\n'
            '        if len(password) < 8:\n'
            '            raise ValidationError("Password must be at least 8 characters")\n'
            '        if user_exists(username):\n'
            '            raise ValidationError("User already exists")\n'
            '        create_user(username, password)\n'
            '        return {"success": True, "message": "Signup successful", "username": username}\n'
            '    except (ValidationError, ValueError) as e:\n'
            '        return {"success": False, "error": str(e)}\n'
        )
        patch = call("edit_code", {"file": "auth/service.py", "function_name": fname, "new_code": new_code})
        print(f"  Patch applied: {patch}")
    else:
        print("  Already patched — no edit needed.")
