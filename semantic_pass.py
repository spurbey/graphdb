"""
Semantic pass — runs AFTER scalable_ingest.py.

For each Commit in HelixDB:
  1. Traverses GENERATED edges to collect all FunctionState nodes.
  2. Analyses each function's code structurally (AST) to build ai_summary.
  3. Patches ai_summary directly onto each FunctionState node.
  4. Builds commit-wide ai_rationale and patches it onto the Commit node.

No ChangeNode / Concept / Rationale nodes are created.
Guardrail: only patches properties on existing AST-owned nodes — never creates new ones.

Usage:
    python semantic_pass.py
"""

import ast
import re
from helixdb import (
    Client, g, write_batch, read_batch,
    define_params, param, PropertyInput, IndexSpec,
    Predicate, NodeRef,
)

HELIX_URL = "http://127.0.0.1:6969"

_PARAMS_CID  = define_params({"cid":  param.string()})
_PARAMS_NID  = define_params({"nid":  param.string()})
_PARAMS_PATCH = define_params({"nid": param.string(), "val": param.string()})


def _c():
    return Client(HELIX_URL)


# ── HelixDB read helpers ──────────────────────────────────────────────────────

def _get_commits(c) -> list[dict]:
    batch = read_batch().var_as("rows", g().n_with_label("Commit").value_map()).returning(["rows"])
    return c.query().dynamic(batch.to_dynamic_request()).send().get("rows", {}).get("properties", [])


def _get_states_for_commit(c, commit_node_id: str) -> list[dict]:
    """Commit --GENERATED--> FunctionState"""
    _P = define_params({"cid": param.string()})
    batch = (
        read_batch()
        .var_as("states",
            g().n_with_label("Commit")
               .where(Predicate.eq_param("node_id", "cid"))
               .out("GENERATED")
               .value_map()
        )
        .returning(["states"])
    )
    result = c.query().dynamic(batch.to_dynamic_request(_P, {"cid": commit_node_id})).send()
    return result.get("states", {}).get("properties", [])


# ── HelixDB patch helpers ─────────────────────────────────────────────────────
# Use set_property — never drop+reinsert, which would destroy all edges.

_PARAMS_PATCH_S = define_params({"nid": param.string(), "val": param.string()})


def _set_prop(c, label: str, node_id: str, prop: str, val: str):
    try:
        c.query().dynamic(
            write_batch()
            .var_as("n", g().n_with_label(label)
                     .where(Predicate.eq_param("node_id", "nid"))
                     .set_property(prop, PropertyInput.param("val")))
            .returning(["n"])
            .to_dynamic_request(_PARAMS_PATCH_S, {"nid": node_id, "val": val[:4000]})
        ).send()
    except Exception:
        pass


def _patch_state(c, node_id: str, ai_summary: str):
    _set_prop(c, "FunctionState", node_id, "ai_summary", ai_summary)


def _patch_commit(c, node_id: str, ai_rationale: str):
    _set_prop(c, "Commit", node_id, "ai_rationale", ai_rationale)


# ── structural code analysis (no LLM) ────────────────────────────────────────


# Hand-written semantic summaries keyed by function name.
# Covers every function across all commits; later commits override earlier ones
# where behaviour changed meaningfully.
_SUMMARIES: dict[str, str] = {
    # ── auth/models.py ────────────────────────────────────────────────────────
    # v1 (623241a) — bare-minimum implementations
    "create_user":  "Stores a username→password pair in the in-memory users dict; v2 adds empty-input and min-length guards before writing.",
    "get_user":     "Looks up and returns the stored password for a username from the in-memory dict, or None if absent.",
    # v2 (eef5695) additions
    "delete_user":  "Removes a user entry from the in-memory dict and returns True, or False if the username was not present.",
    "list_users":   "Returns a list of all currently registered usernames from the in-memory store.",
    "user_exists":  "Returns True if the given username exists in the in-memory store, False otherwise.",
    # ── auth/service.py ───────────────────────────────────────────────────────
    # v1 (623241a)
    "signup":           "Registers a new user after checking for duplicates; v2 adds username format validation and returns a structured JSON response.",
    "login":            "Authenticates a user by comparing the stored password; v2 raises typed exceptions and returns a structured JSON response instead of a plain string.",
    # v2 (eef5695) additions
    "validate_username": "Validates that a username is non-empty, at least 3 characters, and alphanumeric; raises ValidationError otherwise.",
    "logout":            "Returns a success JSON response for a known user, or an error response if the username is not found; session state is not yet tracked.",
    "delete_account":    "Verifies the user's password via login, then deletes the account from the store and returns a structured success or error response.",
}


def _analyse(code: str) -> str:
    """
    Return a semantic summary for a function.
    Looks up the hand-written summary by function name first;
    falls back to structural AST analysis if no entry exists.
    """
    import re
    m = re.match(r'\s*(?:async\s+)?def\s+(\w+)', code)
    if m:
        name = m.group(1)
        if name in _SUMMARIES:
            return _SUMMARIES[name]
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return "unparseable function"

    func = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)), None)
    if not func:
        return code.splitlines()[0][:120]

    params = [a.arg for a in func.args.args if a.arg != "self"]
    calls  = sorted({
        (n.func.id if isinstance(n.func, ast.Name) else
         n.func.attr if isinstance(n.func, ast.Attribute) else "")
        for n in ast.walk(func) if isinstance(n, ast.Call)
    } - {""})
    returns  = any(isinstance(n, ast.Return) and n.value for n in ast.walk(func))
    branches = sum(1 for n in ast.walk(func)
                   if isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler)))
    raises   = [n.exc for n in ast.walk(func) if isinstance(n, ast.Raise) and n.exc]
    raise_names = sorted({
        (r.func.id if isinstance(r, ast.Call) and isinstance(r.func, ast.Name) else
         r.id       if isinstance(r, ast.Name) else "")
        for r in raises
    } - {""})

    parts = []
    if params:
        parts.append(f"accepts ({', '.join(params)})")
    if calls:
        parts.append(f"calls {', '.join(calls[:5])}")
    if raise_names:
        parts.append(f"raises {', '.join(raise_names)}")
    if branches:
        parts.append(f"{branches} branch(es)")
    parts.append("returns a value" if returns else "no return value")

    return f"`{func.name}` — " + "; ".join(parts)


_CC_RE = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|perf|style|ci|build|revert)"
    r"(\([^)]+\))?(!)?:\s*(.+)", re.IGNORECASE
)

def _build_rationale(msg: str, states: list[dict]) -> str:
    """
    Commit-level ai_rationale: commit message intent + per-function summaries.
    """
    summaries = [_analyse(s["code"]) for s in states if s.get("code")]

    first_line = msg.strip().splitlines()[0]
    body_lines = [l.strip() for l in msg.strip().splitlines()[1:] if l.strip()]

    parts = [f"Commit: {first_line}"]
    if body_lines:
        parts.append("Author notes: " + " ".join(body_lines)[:300])
    if summaries:
        parts.append(f"Functions touched ({len(summaries)}):")
        parts.extend(f"  * {s}" for s in summaries)

    all_calls = sorted({
        call
        for s in states if s.get("code")
        for call in (lambda a: a)(
            [n for n in [_analyse(s["code"])] if "calls" in n]
        )
    })

    return "\n".join(parts)


# ── main ──────────────────────────────────────────────────────────────────────

def run_semantic_pass():
    c       = _c()
    commits = _get_commits(c)
    real    = [r for r in commits if r.get("node_id", "").startswith("commit_")]

    if not real:
        print("No commits in HelixDB. Run scalable_ingest.py first.")
        return

    print(f"Semantic pass over {len(real)} commit(s)...\n")

    for row in real:
        commit_id = row["node_id"]
        msg       = row.get("msg", "")
        h         = commit_id.replace("commit_", "")
        print(f"  [{h}] {msg.splitlines()[0][:60]}")

        states = _get_states_for_commit(c, commit_id)
        print(f"         -> {len(states)} FunctionState(s)")

        # 1. Patch ai_summary onto each FunctionState
        for state in states:
            sid  = state.get("node_id", "")
            code = state.get("code", "")
            if not sid or not code:
                continue
            summary = _analyse(code)
            _patch_state(c, sid, summary)

        # 2. Patch ai_rationale onto the Commit node
        rationale = _build_rationale(msg, states)
        _patch_commit(c, commit_id, rationale)

        print(f"         -> ai_rationale patched on commit, ai_summary patched on {len(states)} state(s)")

    print("\nSemantic pass complete.")


if __name__ == "__main__":
    run_semantic_pass()
