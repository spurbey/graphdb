def check_search(task: dict, result: dict) -> dict:
    gt = task["ground_truth"]
    returned_ids = [n.get("id", "") for n in result.get("nodes", [])]
    returned_names = [n.get("name", "") for n in result.get("nodes", [])]
    checks = {}

    if "expected_substrings" in gt:
        subs = gt["expected_substrings"]
        matches = [nid for nid in returned_ids for s in subs if s.lower() in nid.lower()]
        checks["topic_match_count"] = len(set(matches))
        checks["topic_match_any"] = checks["topic_match_count"] > 0

    if "expected_file_hint" in gt:
        hint = gt["expected_file_hint"].lower()
        file_matches = [nid for nid in returned_ids if hint in nid.lower()]
        checks["file_hint_match_count"] = len(file_matches)
        checks["file_hint_any"] = checks["file_hint_match_count"] > 0

    checks["nodes_returned"] = len(returned_ids)
    checks["pass"] = checks.get("topic_match_any", False) or checks.get("file_hint_any", False)
    return checks


def check_explain_coupling(task: dict, result: dict) -> dict:
    gt = task["ground_truth"]
    checks = {}

    if result is None:
        checks["pass"] = False
        checks["error"] = "explain_coupling returned None"
        return checks

    category = result.get("category", result.get("ast_relation_type", ""))
    count = result.get("co_change_count", result.get("occurrence_count", 0))

    if "expected_category" in gt:
        checks["category_correct"] = category == gt["expected_category"]
    if "min_occurrence_count" in gt:
        checks["occurrence_adequate"] = count >= gt["min_occurrence_count"]
    if "max_jaccard_lower_bound" in gt:
        jaccard = result.get("jaccard", 0)
        checks["jaccard_reasonable"] = jaccard >= gt["max_jaccard_lower_bound"]

    checks["pass"] = all(v for k, v in checks.items() if k != "pass")
    return checks


def check_commit_review(task: dict, result: list) -> dict:
    gt = task["ground_truth"]
    checks = {}

    if not result:
        checks["pass"] = False
        checks["error"] = "commit_review returned empty list"
        return checks

    first = result[0] if isinstance(result, list) else result

    if "expected_layers" in gt:
        for layer in gt["expected_layers"]:
            val = first.get(layer, first.get(layer.replace("_", ""), None))
            checks[f"layer_{layer}"] = val is not None
    if "expect_blast_nonempty" in gt:
        blast = first.get("blast_radius", first.get("blast", []))
        checks["blast_nonempty"] = len(blast) > 0 if isinstance(blast, list) else bool(blast)

    checks["pass"] = all(v for k, v in checks.items() if k != "pass")
    return checks


def check(task: dict, result, condition: str = "treatment") -> dict:
    capability = task["capability"]
    if capability == "semantic_search":
        return check_search(task, result)
    elif capability == "explain_coupling":
        return check_explain_coupling(task, result)
    elif capability == "commit_review":
        return check_commit_review(task, result)
    return {"pass": False, "error": f"unknown capability: {capability}"}
