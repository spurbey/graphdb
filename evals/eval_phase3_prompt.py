"""
Phase 3 Eval: Verify system prompt produces consistent classifications.

Tests the prompt's decision tree against known examples.
These are ground-truth cases — if the LLM gets these wrong, the prompt needs tightening.

Runs against the OpenRouter API to test actual LLM output.
Can be run without HelixDB.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load API key
key = ""
for p in [ROOT / ".env"]:
    if not p.exists(): continue
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if "api" in k.lower():
                key = v.strip(); break
    if key: break

if not key:
    print("No API key found — skipping LLM tests")
    sys.exit(0)

# Load system prompt
prompt_path = ROOT / "skills" / "annotate_commit_prompt.md"
system_prompt = prompt_path.read_text(encoding="utf-8")

# Test cases: (description, diff, commit_msg, expected_edge_type, expected_skip)
TEST_CASES = [
    {
        "name": "bug fix — obvious from commit message and diff",
        "diff": """
-    if not payload:
-        return {"event_id": None, "session_id": "default"}
+    if not payload:
+        return {"event_id": None, "session_id": payload.get("session_id", "default"), "skipped": True}
""",
        "commit_msg": "fix: return correct session_id when payload is empty",
        "func_name": "ingest_hook_payload",
        "is_new": False,
        "expected_edge_type": "FIXED",
        "expected_skip": False,
    },
    {
        "name": "new parameter added — EXTENDED",
        "diff": """
-def memory_context_pack(session_id: str) -> dict:
+def memory_context_pack(session_id: str, cross_encoder: bool = False) -> dict:
     memories = search_memories(session_id)
+    if cross_encoder:
+        memories = rerank(memories)
     return build_context_pack(memories)
""",
        "commit_msg": "feat: add cross encoder rerank option to context pack",
        "func_name": "memory_context_pack",
        "is_new": False,
        "expected_edge_type": "EXTENDED",
        "expected_skip": False,
    },
    {
        "name": "trivial whitespace change — should be SKIPPED",
        "diff": """
-def normalize_agent(name):
-    return name.strip().lower()
+def normalize_agent(name):
+    return name.strip().lower()
""",
        "commit_msg": "style: fix indentation",
        "func_name": "normalize_agent",
        "is_new": False,
        "expected_edge_type": None,
        "expected_skip": True,
    },
    {
        "name": "internal restructure same behavior — REFACTORED",
        "diff": """
-def process_event(self, payload):
-    session = self.get_session(payload["session_id"])
-    event = self.create_event(session, payload)
-    self.extract_memories(event)
-    return event
+def process_event(self, payload):
+    return self._run_pipeline(payload)
+
+def _run_pipeline(self, payload):
+    session = self.get_session(payload["session_id"])
+    event = self.create_event(session, payload)
+    self.extract_memories(event)
+    return event
""",
        "commit_msg": "refactor: extract pipeline logic into _run_pipeline",
        "func_name": "process_event",
        "is_new": False,
        "expected_edge_type": "REFACTORED",
        "expected_skip": False,
    },
]


def ask_llm(system: str, user: str) -> str:
    # Truncate system prompt to stay within token limits but keep enough
    # to include the decision tree, output format, and dry-run mode instructions
    system_short = system[:6000] if len(system) > 6000 else system
    payload = json.dumps({
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "messages": [
            {"role": "system", "content": system_short},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1000,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return resp["choices"][0]["message"]["content"]


print("Phase 3 Eval: System prompt consistency")
print("=" * 60)
print(f"Testing {len(TEST_CASES)} cases against LLM...")
print()

passed = 0
failed = 0

for tc in TEST_CASES:
    print(f"  Case: {tc['name']}")

    user_prompt = f"""
Commit message: {tc['commit_msg']}
Function: {tc['func_name']} (is_new={tc['is_new']})

Diff:
{tc['diff']}

DRY-RUN MODE: Classify this change and reply with ONLY a JSON object. No other text.
{{"skip": bool, "edge_type": "REDESIGNED|FIXED|EXTENDED|REFACTORED|null", "memory": "...", "reason": "one sentence"}}
"""

    try:
        response = ask_llm(system_prompt, user_prompt)

        # Extract first valid JSON object from response (handles nested braces)
        def _extract_json(text):
            depth = 0
            start = -1
            for i, c in enumerate(text):
                if c == '{':
                    if depth == 0:
                        start = i
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidate = text[start:i+1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
            return None

        result = _extract_json(response)
        if result:
            skip = result.get("skip", False)
            edge_type = result.get("edge_type")

            if tc["expected_skip"]:
                if skip:
                    print(f"    PASS: correctly skipped (trivial)")
                    passed += 1
                else:
                    print(f"    FAIL: should have skipped but got edge_type={edge_type}")
                    print(f"    reason: {result.get('reason', '?')}")
                    failed += 1
            else:
                if not skip and edge_type == tc["expected_edge_type"]:
                    print(f"    PASS: edge_type={edge_type}")
                    passed += 1
                else:
                    print(f"    FAIL: expected {tc['expected_edge_type']}, got skip={skip} edge_type={edge_type}")
                    print(f"    reason: {result.get('reason', '?')}")
                    failed += 1
        else:
            print(f"    FAIL: could not parse JSON from response: {response[:200]}")
            failed += 1
    except Exception as e:
        print(f"    ERROR: {e}")
        failed += 1

print()
print(f"Results: {passed}/{len(TEST_CASES)} passed, {failed} failed")
if failed == 0:
    print("Phase 3 Eval: PASS")
else:
    print("Phase 3 Eval: FAIL — tighten decision tree in system prompt")
    print("For each failed case, identify which rule in the decision tree was ambiguous")
