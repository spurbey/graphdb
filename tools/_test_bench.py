import sys, time, json
sys.path.insert(0, "tools")
from helixdb import (Client, g, read_batch, Predicate, Projection,
                     define_params, param, RepeatConfig, SubTraversal)

c = Client("http://127.0.0.1:6969")
_P = define_params({"fid": param.string()})
ANCHOR = "func_auth_models_get_user"
RUNS   = 10

# ── OLD WAY: N+1 round-trips ──────────────────────────────────────────────────
def old_3hop(fid):
    results = []
    current_ids = [fid]
    for _ in range(3):
        next_ids = []
        for node_id in current_ids:
            batch = (read_batch()
                .var_as("c",
                    g().n_with_label("FunctionIdentity")
                       .where(Predicate.eq_param("node_id", "fid"))
                       .in_("CALLS")
                       .project([Projection.property("node_id"), Projection.property("name")])
                )
                .returning(["c"]))
            r = c.query().dynamic(batch.to_dynamic_request(_P, {"fid": node_id})).send()
            nodes = r.get("c", {}).get("properties", [])
            results.extend(nodes)
            next_ids.extend(n["node_id"] for n in nodes)
        current_ids = next_ids
        if not current_ids:
            break
    return results

# ── NEW WAY: repeat().emit_all() — one Rust query ─────────────────────────────
def new_3hop(fid):
    batch = (read_batch()
        .var_as("callers",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("node_id", "fid"))
               .repeat(
                   RepeatConfig.new(SubTraversal.new().in_("CALLS"))
                               .times(3)
                               .emit_all()
               )
               .dedup()
               .project([Projection.property("node_id"), Projection.property("name")])
        )
        .returning(["callers"]))
    r = c.query().dynamic(batch.to_dynamic_request(_P, {"fid": fid})).send()
    return r.get("callers", {}).get("properties", [])

# ── Benchmark ─────────────────────────────────────────────────────────────────
t0 = time.perf_counter()
for _ in range(RUNS):
    old_result = old_3hop(ANCHOR)
old_ms = (time.perf_counter() - t0) / RUNS * 1000

t0 = time.perf_counter()
for _ in range(RUNS):
    new_result = new_3hop(ANCHOR)
new_ms = (time.perf_counter() - t0) / RUNS * 1000

print("Anchor: get_user  |  depth=3  |  averaged over", RUNS, "runs")
print()
print("OLD — N+1 Python loops (up to 3 HTTP round-trips):")
print("  avg", round(old_ms, 2), "ms")
print("  nodes:", [n["name"] for n in old_result])
print()
print("NEW — repeat().emit_all() single Rust traversal (1 HTTP round-trip):")
print("  avg", round(new_ms, 2), "ms")
print("  nodes:", [n["name"] for n in new_result])
print()
if new_result:
    print("Speedup:", round(old_ms / new_ms, 1), "x faster")
else:
    print("NOTE: repeat() returned empty — checking SDK support...")
    # fallback: show what the request looks like
    req = json.loads(
        read_batch()
        .var_as("c",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq_param("node_id", "fid"))
               .repeat(RepeatConfig.new(SubTraversal.new().in_("CALLS")).times(3).emit_all())
               .dedup()
               .project([Projection.property("name")])
        )
        .returning(["c"])
        .to_dynamic_request(_P, {"fid": ANCHOR})
        .to_json_string()
    )
    print("  Steps in request:")
    for step in req["query"]["queries"][0]["Query"]["steps"]:
        print("   ", json.dumps(step))

print()
print("Final wire format (single request, all steps):")
req = json.loads(
    read_batch()
    .var_as("c",
        g().n_with_label("FunctionIdentity")
           .where(Predicate.eq_param("node_id", "fid"))
           .repeat(RepeatConfig.new(SubTraversal.new().in_("CALLS")).times(3).emit_all())
           .dedup()
           .project([Projection.property("name")])
    )
    .returning(["c"])
    .to_dynamic_request(_P, {"fid": ANCHOR})
    .to_json_string()
)
for step in req["query"]["queries"][0]["Query"]["steps"]:
    print(" ", json.dumps(step))
