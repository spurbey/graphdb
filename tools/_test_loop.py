import urllib.request, json, textwrap

def call(tool, params):
    payload = json.dumps({"name": tool, "parameters": params}).encode()
    req = urllib.request.Request("http://127.0.0.1:7700/call", data=payload, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req).read())

# STEP 1: Find stale callers of create_user before the security upgrade
print("=== STEP 1: Find stale callers ===")
r1 = call("get_temporal_vulnerability_trace", {
    "target_func": "create_user",
    "timestamp_iso": "2026-06-25T00:00:00+00:00"
})
print(json.dumps(r1, indent=2))

# STEP 2: Time-travel diff on signup
print("\n=== STEP 2: Time-travel diff on signup ===")
r2 = call("get_code_time_travel_diff", {"state_node_id": "state_auth_service_signup_eef5695"})
print("CURRENT summary:", r2["result"]["current"]["ai_summary"])
print("PREVIOUS code:\n", r2["result"]["previous"]["code"])

# STEP 3: Patch signup — raise min password length to 8
new_signup = (
    'def signup(username, password):\n'
    '    """Register a new user with validation and password strength check."""\n'
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
print("\n=== STEP 3: Patch signup ===")
r3 = call("edit_code", {
    "file": "auth/service.py",
    "function_name": "signup",
    "new_code": new_signup
})
print(json.dumps(r3, indent=2))

# STEP 4: Verify graph updated
print("\n=== STEP 4: Verify graph reflects patch ===")
r4 = call("search_code_semantics", {"prompt": "signup password validation", "k": 1})
print(json.dumps(r4, indent=2))
