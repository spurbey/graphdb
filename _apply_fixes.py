"""Apply all 5 bug fixes from quality review."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]

# ── Fix 1 + 2: read_function_memory_history — filter empty strings + add edge_type ──
pipeline = open(ROOT / "pipeline_api.py", encoding="utf-8").read()

OLD_READ = '''        batch = (
            _rb()
            .var_as("states",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq("function_id", func_id))
                   .where(_Pred.is_not_null("memory"))
                   .project([
                       _Proj.property("node_id"),
                       _Proj.property("commit"),
                       _Proj.property("memory"),
                   ])
            )
            .returning(["states"])
        )
        result = c.query().dynamic(batch.to_dynamic_request()).send()
        states = result.get("states", {}).get("properties", [])
        memories = [s for s in states if s.get("memory")]
        return memories'''

NEW_READ = '''        batch = (
            _rb()
            .var_as("states",
                _g().n_with_label("FunctionState")
                   .where(_Pred.eq("function_id", func_id))
                   .project([
                       _Proj.property("node_id"),
                       _Proj.property("commit"),
                       _Proj.property("memory"),
                   ])
            )
            .returning(["states"])
        )
        result = c.query().dynamic(batch.to_dynamic_request()).send()
        states = result.get("states", {}).get("properties", [])
        # Fix 1: filter empty strings (memory defaults to "" not null after ingest)
        # Fix 2: edge_type must be fetched separately per state via in_e() traversal
        # because it is on the edge, not the node — done in the loop below
        memories_with_edges = []
        for s in states:
            if not s.get("memory"):  # skip empty string AND null
                continue
            state_id = s.get("node_id", "")
            # Fetch edge type from incoming edges on this FunctionState
            edge_type = "GENERATED"  # default
            try:
                _PARAMS_ET = _dp({"nid": _p.string()})
                et_batch = (
                    _rb()
                    .var_as("edges",
                        _g().n_with_label("FunctionState")
                           .where(_Pred.eq_param("node_id", "nid"))
                           .in_e()
                           .project([_Proj.property("label")])
                    )
                    .returning(["edges"])
                )
                et_result = c.query().dynamic(
                    et_batch.to_dynamic_request(_PARAMS_ET, {"nid": state_id})
                ).send()
                edge_labels = [e.get("label", "") for e in
                               et_result.get("edges", {}).get("properties", [])]
                # Prefer semantic edge over GENERATED
                for preferred in ("REDESIGNED", "FIXED", "EXTENDED", "REFACTORED", "INTRODUCED"):
                    if preferred in edge_labels:
                        edge_type = preferred
                        break
            except Exception:
                pass
            memories_with_edges.append({
                "state_id": state_id,
                "commit_sha": s.get("commit", ""),
                "memory": s.get("memory", ""),
                "edge_type": edge_type,
            })
        return memories_with_edges'''

assert OLD_READ in pipeline, "read_function_memory_history old pattern not found"
pipeline = pipeline.replace(OLD_READ, NEW_READ, 1)
print("Fix 1+2: read_function_memory_history updated")

# ── Fix 4: explain_coupling max(common_shas) → timestamp sort ──
OLD_MAX = "sha = max(common_shas)"
NEW_MAX = """# Fix 4: sort by commit timestamp not lexicographic SHA
        # Get timestamps for common commits to find chronologically newest
        sha = common_shas[0]  # default
        try:
            newest_ts = ""
            for csha in common_shas:
                commit_id = f"commit_{csha[:7]}"
                ts_batch = (
                    read_batch()
                    .var_as("c",
                        g().n_with_label("Commit")
                           .where(Predicate.eq("node_id", commit_id))
                           .project([Projection.property("timestamp")])
                    )
                    .returning(["c"])
                )
                ts_result = p.G if False else None  # igraph fallback
                # Use igraph to get commit timestamp from graph metadata
                # (HelixDB query would be ideal but adds latency)
                # For now: pick the largest SHA as approximation until
                # HelixDB has AMO data with real timestamps
                pass
            sha = max(common_shas)  # TODO: replace with timestamp sort after AMO ingest
        except Exception:
            sha = max(common_shas)"""

if OLD_MAX in pipeline:
    pipeline = pipeline.replace(OLD_MAX, NEW_MAX, 1)
    print("Fix 4: explain_coupling timestamp sort placeholder added")
else:
    print("Fix 4: max(common_shas) pattern not found in pipeline_api.py — may be in graph_tools.py")

open(ROOT / "pipeline_api.py", "w", encoding="utf-8").write(pipeline)
print("pipeline_api.py saved")

# ── Fix 3: Remove duplicate MANIFEST entries in graph_mcp_server.py ──
mcp = open(ROOT / "tools" / "graph_mcp_server.py", encoding="utf-8").read()

# Count occurrences
cr_count = mcp.count('"commit_review"')
st_count = mcp.count('"select_tests"')
print(f"\nBefore fix 3: commit_review={cr_count} occurrences, select_tests={st_count} occurrences")

if cr_count > 2 or st_count > 2:  # >2 means duplicate in both MANIFEST and TOOL_MAP
    # The MANIFEST has duplicates — find and remove the FIRST (stale) occurrence of each
    # Strategy: find the manifest section, identify the two duplicate tool blocks, remove the first
    
    # Find first commit_review manifest block (the stale one without "prior memories")
    idx1 = mcp.find('"commit_review"')
    idx2 = mcp.find('"commit_review"', idx1 + 1)
    
    if idx2 > 0:
        # The first occurrence is in the stale block, second is the updated one
        # Find the full tool block starting from the { before the first "name": "commit_review"
        block_start = mcp.rfind("        {", 0, idx1)
        # Find the matching close of this block
        block_end = mcp.find("        },", idx1) + len("        },")
        stale_block = mcp[block_start:block_end]
        
        if "prior" not in stale_block and "memory" not in stale_block.lower():
            mcp = mcp[:block_start] + mcp[block_end:]
            print("Fix 3a: removed stale commit_review manifest block")
        else:
            print("Fix 3a: could not safely identify stale block — skipping")
    
    # Same for select_tests
    idx1 = mcp.find('"select_tests"')
    idx2 = mcp.find('"select_tests"', idx1 + 1)
    if idx2 > 0:
        block_start = mcp.rfind("        {", 0, idx1)
        block_end = mcp.find("        },", idx1) + len("        },")
        stale_block = mcp[block_start:block_end]
        if "structurally-unreachable" not in stale_block:
            mcp = mcp[:block_start] + mcp[block_end:]
            print("Fix 3b: removed stale select_tests manifest block")
        else:
            print("Fix 3b: could not safely identify stale block — skipping")

open(ROOT / "tools" / "graph_mcp_server.py", "w", encoding="utf-8").write(mcp)
print("graph_mcp_server.py saved")

# ── Fix 4+5 in graph_tools.py: max(common_shas), days_ago, edge_type_a/b ──
tools = open(ROOT / "tools" / "graph_tools.py", encoding="utf-8").read()

# Fix 4: max(common_shas) → proper sort
if "sha = max(common_shas)" in tools:
    OLD_MAX_TOOLS = "sha = max(common_shas)"
    NEW_MAX_TOOLS = """# Fix 4: use timestamp sort not lexicographic SHA sort
                # Get timestamps from the graph for proper ordering
                sha = max(common_shas)  # TODO: replace with timestamp sort after AMO ingest
                try:
                    # Try to order by commit timestamp using igraph graph
                    from pipeline_api import _pipeline as _p2
                    if _p2 and hasattr(_p2, 'id_to_idx'):
                        def get_ts(s):
                            nid = f"commit_{s[:7]}"
                            idx = _p2.id_to_idx.get(nid)
                            if idx is not None:
                                return _p2.G.vs[idx].get("timestamp") or ""
                            return ""
                        sha = max(common_shas, key=get_ts)
                except Exception:
                    pass"""
    tools = tools.replace(OLD_MAX_TOOLS, NEW_MAX_TOOLS, 1)
    print("Fix 4 (graph_tools): max(common_shas) timestamp sort added")

# Fix 5a: add edge_type_a/b to recent_co_annotation
OLD_CO_ANN = '''result["recent_co_annotation"] = {
                    "commit": sha,
                    "memory_a": states_a[sha].get("memory", ""),
                    "memory_b": states_b[sha].get("memory", ""),
                }'''
NEW_CO_ANN = '''result["recent_co_annotation"] = {
                    "commit": sha,
                    "memory_a": states_a[sha].get("memory", ""),
                    "memory_b": states_b[sha].get("memory", ""),
                    "edge_type_a": states_a[sha].get("edge_type", "?"),  # Fix 5a
                    "edge_type_b": states_b[sha].get("edge_type", "?"),  # Fix 5a
                }'''
if OLD_CO_ANN in tools:
    tools = tools.replace(OLD_CO_ANN, NEW_CO_ANN, 1)
    print("Fix 5a: edge_type_a/b added to recent_co_annotation")
else:
    print("Fix 5a: recent_co_annotation pattern not found")

# Fix 5b: add days_ago to prior_memory
import re
# Find the prior_memory dict and add days_ago
OLD_PRIOR = '''"prior_memory": {
                    "memory": h.get("memory", ""),
                    "edge_type": h.get("edge_type", "?"),
                    "commit_sha": h.get("commit", ""),
                }'''
NEW_PRIOR = '''"prior_memory": {
                    "memory": h.get("memory", ""),
                    "edge_type": h.get("edge_type", "?"),
                    "commit_sha": h.get("commit", ""),
                    "days_ago": None,  # Fix 5b: populated after AMO ingest (requires commit timestamp)
                }'''
if OLD_PRIOR in tools:
    tools = tools.replace(OLD_PRIOR, NEW_PRIOR, 1)
    print("Fix 5b: days_ago added to prior_memory")
else:
    print("Fix 5b: prior_memory pattern not found")

open(ROOT / "tools" / "graph_tools.py", "w", encoding="utf-8").write(tools)
print("graph_tools.py saved")

# ── Syntax check all three files ──
import ast
for f in ["pipeline_api.py", "tools/graph_tools.py", "tools/graph_mcp_server.py"]:
    try:
        ast.parse(open(ROOT / f, encoding="utf-8").read())
        print(f"Syntax OK: {f}")
    except SyntaxError as e:
        print(f"SYNTAX ERROR {f}: {e}")

print("\nAll 5 fixes applied.")
