"""Count test function contamination in ablation_results.json."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "sandbox" / "ablation_results.json").read_text(encoding="utf-8"))


def is_test(name: str) -> bool:
    n = name.lower()
    return n.startswith("test_") or "[test_" in n


modes = ["top10_vector", "top10_calls_ppr", "top10_theme_overlay"]
labels = ["VectorOnly", "CallsPPR", "ThemeOverlay"]
rkeys = ["rank_vector_only", "rank_calls_ppr", "rank_theme_overlay"]

print("TEST FUNCTION CONTAMINATION ANALYSIS")
print("=" * 70)

for mode, label, rkey in zip(modes, labels, rkeys):
    total_slots = 0
    test_slots = 0
    hits_that_are_tests = 0

    for q in data["per_query"]:
        results = q.get(mode, [])
        rank = q.get(rkey)
        test_count = sum(1 for r in results if is_test(r))
        total_slots += len(results)
        test_slots += test_count
        if rank is not None and rank <= len(results):
            hit_fn = results[rank - 1]
            if is_test(hit_fn):
                hits_that_are_tests += 1
                print(f"  WARNING: HIT IS TEST FUNCTION [{label}] query={q['id']}: {hit_fn}")

    pct = 100 * test_slots / total_slots if total_slots else 0
    print(f"{label}: {test_slots}/{total_slots} slots occupied by test functions ({pct:.1f}%), {hits_that_are_tests} HITs are test functions")

print()
print("PER QUERY TEST SLOT COUNT (vec / ppr / theme):")
for q in data["per_query"]:
    vec_tests = sum(1 for r in q.get("top10_vector", []) if is_test(r))
    ppr_tests = sum(1 for r in q.get("top10_calls_ppr", []) if is_test(r))
    thm_tests = sum(1 for r in q.get("top10_theme_overlay", []) if is_test(r))
    qid = q["id"]
    print(f"  {qid:<40} vec={vec_tests}  ppr={ppr_tests}  thm={thm_tests}")

print()
print("ADJUSTED HIT@10 IF TEST FUNCTIONS WERE EXCLUDED FROM CANDIDATE POOL:")
print("(i.e., would the target still rank in top-10 if test functions weren't there?)")
for mode, label, rkey in zip(modes, labels, rkeys):
    real_hits = 0
    adjusted_misses = []
    for q in data["per_query"]:
        results = q.get(mode, [])
        rank = q.get(rkey)
        if rank is not None and rank <= len(results):
            hit_fn = results[rank - 1]
            if not is_test(hit_fn):
                real_hits += 1
            else:
                adjusted_misses.append((q["id"], hit_fn))
    original = data["summary"].get("hit_at_10_" + rkey.replace("rank_", ""), "?")
    print(f"  {label}: {real_hits}/10  (original: {original}/10)")
    for qid, fn in adjusted_misses:
        print(f"    adjusted miss: {qid} -> hit was '{fn}'")

print()
print("GOD-NODE FREQUENCY (close, make_settings, init_db, health_ping, add_event):")
god_nodes = {"close", "make_settings", "init_db", "health_ping", "add_event", "_utc_now"}
for mode, label in zip(modes, labels):
    god_appearances = {}
    for q in data["per_query"]:
        results = q.get(mode, [])
        for r in results:
            fname = r.split(" [")[0]
            if fname in god_nodes:
                god_appearances[fname] = god_appearances.get(fname, 0) + 1
    print(f"  {label}: {dict(sorted(god_appearances.items(), key=lambda x: -x[1]))}")
