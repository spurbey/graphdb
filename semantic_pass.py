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
    batch = (
        read_batch()
        .var_as("commit", g().n_with_label("Commit").where(Predicate.eq_param("node_id", "cid")))
        .var_as("states", g().n(NodeRef.var("commit")).out_e("GENERATED").out_n().value_map())
        .returning(["states"])
    )
    result = c.query().dynamic(batch.to_dynamic_request(_PARAMS_CID, {"cid": commit_node_id})).send()
    return result.get("states", {}).get("properties", [])


# ── HelixDB patch helpers ─────────────────────────────────────────────────────

def _patch_state(c, node_id: str, ai_summary: str):
    """Write ai_summary onto an existing FunctionState node by dropping + re-inserting."""
    # Read current props first
    batch = (
        read_batch()
        .var_as("n", g().n_with_label("FunctionState").where(Predicate.eq_param("node_id", "nid")).value_map())
        .returning(["n"])
    )
    result = c.query().dynamic(batch.to_dynamic_request(_PARAMS_NID, {"nid": node_id})).send()
    rows = result.get("n", {}).get("properties", [])
    if not rows:
        return
    props = {k: v for k, v in rows[0].items() if not k.startswith("$")}
    props["ai_summary"] = ai_summary

    # Drop old, re-insert with updated props
    del_batch = (
        write_batch()
        .var_as("n", g().n_with_label("FunctionState").where(Predicate.eq_param("node_id", "nid")).drop())
        .returning(["n"])
    )
    try:
        c.query().dynamic(del_batch.to_dynamic_request(_PARAMS_NID, {"nid": node_id})).send()
    except Exception:
        pass

    all_props = {"node_id": PropertyInput.value(node_id),
                 **{k: PropertyInput.value(str(v)[:4000]) for k, v in props.items()}}
    try:
        c.query().dynamic(
            write_batch().var_as("n", g().add_n("FunctionState", all_props)).returning(["n"]).to_dynamic_request()
        ).send()
    except Exception:
        pass


def _patch_commit(c, node_id: str, ai_rationale: str):
    """Write ai_rationale onto an existing Commit node by dropping + re-inserting."""
    batch = (
        read_batch()
        .var_as("n", g().n_with_label("Commit").where(Predicate.eq_param("node_id", "nid")).value_map())
        .returning(["n"])
    )
    result = c.query().dynamic(batch.to_dynamic_request(_PARAMS_NID, {"nid": node_id})).send()
    rows = result.get("n", {}).get("properties", [])
    if not rows:
        return
    props = {k: v for k, v in rows[0].items() if not k.startswith("$")}
    props["ai_rationale"] = ai_rationale

    del_batch = (
        write_batch()
        .var_as("n", g().n_with_label("Commit").where(Predicate.eq_param("node_id", "nid")).drop())
        .returning(["n"])
    )
    try:
        c.query().dynamic(del_batch.to_dynamic_request(_PARAMS_NID, {"nid": node_id})).send()
    except Exception:
        pass

    all_props = {"node_id": PropertyInput.value(node_id),
                 **{k: PropertyInput.value(str(v)[:4000]) for k, v in props.items()}}
    try:
        c.query().dynamic(
            write_batch().var_as("n", g().add_n("Commit", all_props)).returning(["n"]).to_dynamic_request()
        ).send()
    except Exception:
        pass


# ── structural code analysis (no LLM) ────────────────────────────────────────

def _analyse(code: str) -> str:
    """Return a single-sentence mechanical description of a function from its AST."""
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
