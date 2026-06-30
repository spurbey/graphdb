import sys
sys.path.insert(0, "tools")
from graph_tools import search_code_semantics
from helixdb import Client, g, read_batch, Predicate, Projection

c = Client("http://127.0.0.1:6969")

def get_callers(func_id):
    r = c.query().dynamic(
        read_batch()
        .var_as("callers",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq("node_id", func_id))
               .in_("CALLS")
               .project([Projection.property("node_id"),
                         Projection.property("name"),
                         Projection.property("file")])
        )
        .returning(["callers"])
        .to_dynamic_request()
    ).send()
    return r.get("callers", {}).get("properties", [])

def get_summary(func_id):
    r = c.query().dynamic(
        read_batch()
        .var_as("s",
            g().n_with_label("FunctionIdentity")
               .where(Predicate.eq("node_id", func_id))
               .out("HAS_STATE")
               .where(Predicate.eq("status", "active"))
               .project([Projection.property("ai_summary")])
        )
        .returning(["s"])
        .to_dynamic_request()
    ).send()
    props = r.get("s", {}).get("properties", [])
    return props[0].get("ai_summary", "") if props else ""

# ── Step A: vector search ──────────────────────────────────────────────────────
query = "look up a stored user credential"
print("QUERY:", query)
print()

matches = search_code_semantics(query, k=3)
if not matches or "error" in matches[0]:
    print("No match:", matches)
    exit()

# pick the best match whose name contains "get" or "lookup" — else just take top
anchor = next((m for m in matches if "get" in m["function_id"]), matches[0])
anchor_id   = anchor["function_id"]
anchor_name = anchor_id.replace("func_auth_models_", "").replace("func_auth_service_", "")
print("HOP 0 — Anchor:", anchor_name)
print("        Matched from", len(matches), "candidates. Top match:", matches[0]["function_id"].replace("func_auth_",""))
print("        Summary:", anchor["ai_summary"])
print()

# ── Hop 1 ──────────────────────────────────────────────────────────────────────
hop1 = get_callers(anchor_id)
print("HOP 1 — Who calls", anchor_name + "?", "(" + str(len(hop1)) + " callers)")
for n in hop1:
    s = get_summary(n["node_id"])
    print("  ->", n["name"], "|", s[:65])
print()

# ── Hop 2 ──────────────────────────────────────────────────────────────────────
hop2_map = {}
for n in hop1:
    callers = get_callers(n["node_id"])
    hop2_map[n["name"]] = callers
    print("HOP 2 — Who calls", n["name"] + "?", "(" + str(len(callers)) + " callers)")
    for c2 in callers:
        s = get_summary(c2["node_id"])
        print("  ->", c2["name"], "|", s[:65])
    if not callers:
        print("  (leaf)")
print()

# ── Hop 3 ──────────────────────────────────────────────────────────────────────
for h1_name, hop2_list in hop2_map.items():
    for n in hop2_list:
        callers = get_callers(n["node_id"])
        print("HOP 3 — Who calls", n["name"] + "?", "(" + str(len(callers)) + " callers)")
        for c3 in callers:
            s = get_summary(c3["node_id"])
            print("  ->", c3["name"], "|", s[:65])
        if not callers:
            print("  (leaf — nothing calls", n["name"] + ")")
print()

# ── Final summary ──────────────────────────────────────────────────────────────
print("=" * 55)
print("3-HOP TRAVERSAL RESULT")
print("=" * 55)
print("Query:", repr(query))
print("Resolved to:", anchor_name)
print("Call chains found:")
for h1 in hop1:
    chain = anchor_name + " <- " + h1["name"]
    for h2 in hop2_map.get(h1["name"], []):
        chain += " <- " + h2["name"]
    print(" ", chain)
