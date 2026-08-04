"""Inspect which lexicographic dimension separates the displayed top two roots.

This is a pairwise audit of saved top-ranked rows, not a full counterfactual
ablation. A full ablation must be emitted by the experiment runner over every
candidate, because saved artifacts retain only the top rows for each ranker.
"""

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sandbox" / "out"


def rank_of(rows: list[dict], expected_ids: set[str]) -> int | None:
    for rank, row in enumerate(rows, 1):
        if row.get("id", "") in expected_ids:
            return rank
    return None


def decisive_dimension(case: dict, first: dict, second: dict) -> str:
    if first["coverage"] != second["coverage"]:
        return "coverage"
    if case["direction_mode"] == "common_caller":
        if first["directional_support"] != second["directional_support"]:
            return "direction"
        if first["semantic_raw"] != second["semantic_raw"]:
            return "semantic"
    else:
        if first["semantic_raw"] != second["semantic_raw"]:
            return "semantic"
        if first["directional_support"] != second["directional_support"]:
            return "direction"
    if first["max_distance"] != second["max_distance"]:
        return "max_distance"
    if first["distance_sum"] != second["distance_sum"]:
        return "distance_sum"
    if first["hub_norm"] != second["hub_norm"]:
        return "hub_norm"
    return "deterministic_id"


def analyze_file(artifact: str, label: str) -> None:
    data = json.loads((OUT / artifact).read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    print(f"\n{label} ({len(data['cases'])} cases)")
    print("=" * 72)

    for case in data["cases"]:
        correct_ids = set(case.get("expected_ids", [])) | set(
            case.get("acceptable_ids", [])
        )
        rows = case["top"].get("mode_conditioned_lexicographic", [])
        final_rank = rank_of(rows, correct_ids)
        print(f"\n{case['case_id']} final_rank={final_rank}")
        if len(rows) < 2:
            counts["trivial"] += 1
            print("  pairwise_decisive=trivial")
            continue

        first, second = rows[:2]
        factor = decisive_dimension(case, first, second)
        counts[factor] += 1
        print(
            f"  pairwise_decisive={factor} "
            f"winner={first['name']} runner_up={second['name']}"
        )
        print(
            f"  coverage={first['coverage']}/{second['coverage']} "
            f"direction={first['directional_support']}/{second['directional_support']} "
            f"cosine={first['semantic_raw']:.6f}/{second['semantic_raw']:.6f}"
        )

    print("\nPAIRWISE SUMMARY")
    for factor, count in sorted(counts.items()):
        print(f"  {factor}: {count}")


analyze_file(
    "query_connector_stage_11_union_overlay_training.json", "TRAINING overlay"
)
analyze_file(
    "query_connector_stage_11_union_overlay_holdout.json", "HOLDOUT overlay"
)
