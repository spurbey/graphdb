"""Analyze adversarial case results - did semantics pick correctly when coverage ties?"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sandbox" / "out"

d = json.loads((OUT / "query_connector_adversarial_run2.json").read_text(encoding="utf-8"))
cases = d["cases"]

print(f"ADVERSARIAL CASES ({len(cases)} cases)")
print("=" * 70)

passed = 0
failed = 0
coverage_decided = 0
semantics_decided = 0
semantics_hurt = 0

for case in cases:
    cid = case["case_id"]
    mode = case["direction_mode"]
    top = case["top"]
    exp = set(case.get("expected_ids", [])) | set(case.get("acceptable_ids", []))
    mcl = top.get("mode_conditioned_lexicographic", [])

    final_rank = None
    for i, item in enumerate(mcl):
        if isinstance(item, dict) and item.get("id", "") in exp:
            final_rank = i + 1
            break

    correct = final_rank == 1
    if correct:
        passed += 1
    else:
        failed += 1

    print(f"\n{cid}")
    print(f"  mode={mode}  final_rank={final_rank}  correct={correct}")

    # Show top-5 with coverage and cosine
    for i, item in enumerate(mcl[:5], 1):
        if not isinstance(item, dict):
            continue
        name = item.get("name", "?")
        cos = item.get("semantic_raw")
        cov = item.get("coverage", "?")
        nid = item.get("id", "")
        mark = " <<EXP" if nid in exp else ""
        cos_s = f"{cos:.4f}" if cos is not None else "None"
        print(f"  {i}. {name:<45} cov={cov}  cos={cos_s}{mark}")

    # Diagnose what decided it
    if len(mcl) >= 2 and isinstance(mcl[0], dict) and isinstance(mcl[1], dict):
        r1 = mcl[0]
        r2 = mcl[1]
        r1_cov = r1.get("coverage", 0)
        r2_cov = r2.get("coverage", 0)
        r1_cos = r1.get("semantic_raw") or 0.0
        r2_cos = r2.get("semantic_raw") or 0.0
        r1_correct = r1.get("id", "") in exp

        if r1_cov > r2_cov:
            print(f"  DECIDED BY: coverage ({r1_cov} vs {r2_cov})")
            coverage_decided += 1
        elif abs(r1_cos - r2_cos) > 0.005:
            print(f"  DECIDED BY: cosine ({r1_cos:.4f} vs {r2_cos:.4f})")
            if r1_correct:
                print(f"  => semantics CORRECT: chose right answer")
                semantics_decided += 1
            else:
                print(f"  => semantics WRONG: chose wrong answer (correct is rank {final_rank})")
                semantics_hurt += 1
                # Show expected item
                exp_item = next((c for c in mcl if c.get("id", "") in exp), None)
                if exp_item:
                    exp_cos = exp_item.get("semantic_raw") or 0.0
                    print(f"  => expected cosine={exp_cos:.4f} vs winner cosine={r1_cos:.4f}")
        else:
            print(f"  DECIDED BY: structural tiebreak (same cov={r1_cov}, similar cosine)")

print(f"\n{'='*70}")
print(f"SUMMARY: {passed}/{passed+failed} correct")
print(f"  Coverage decided: {coverage_decided}")
print(f"  Semantics correct: {semantics_decided}")
print(f"  Semantics hurt: {semantics_hurt}")
