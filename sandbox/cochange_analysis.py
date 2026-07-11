from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Iterable


CATEGORY_STRUCTURAL = "structural_redundant"
CATEGORY_SHARED_DEP = "shared_dependency"
CATEGORY_BURST = "temporal_burst"
CATEGORY_SHARED_ONLY = "shared_commit_only"

MODE_GENERAL_RETRIEVAL = "general_retrieval"
MODE_WHY_COUPLED = "why_coupled"
MODE_RISK = "risk"
MODE_PRE_EDIT = "pre_edit_history"


def cochange_consumer_policy(category: str, mode: str) -> dict:
    """Return inclusion/cost policy for a CO_CHANGE category in one consumer mode."""
    if mode == MODE_GENERAL_RETRIEVAL:
        if category in {CATEGORY_SHARED_DEP, CATEGORY_SHARED_ONLY}:
            return {"include": True, "cost_multiplier": 1.0, "display": "ongoing coupling"}
        if category == CATEGORY_STRUCTURAL:
            return {"include": True, "cost_multiplier": 2.0, "display": "historical count on structural edge"}
        return {"include": False, "cost_multiplier": math.inf, "display": "excluded one-time burst"}

    if mode == MODE_WHY_COUPLED:
        include = category in {CATEGORY_SHARED_DEP, CATEGORY_SHARED_ONLY}
        return {
            "include": include,
            "cost_multiplier": 1.0 if include else math.inf,
            "display": "why-coupled evidence" if include else "excluded explained/non-recurring edge",
        }

    if mode == MODE_RISK:
        if category in {CATEGORY_SHARED_DEP, CATEGORY_SHARED_ONLY}:
            return {"include": True, "cost_multiplier": 1.0, "display": "risk coupling"}
        if category == CATEGORY_STRUCTURAL:
            return {"include": True, "cost_multiplier": 2.5, "display": "lower-weight structural history"}
        return {"include": False, "cost_multiplier": math.inf, "display": "excluded one-time burst"}

    if mode == MODE_PRE_EDIT:
        if category == CATEGORY_BURST:
            return {"include": True, "cost_multiplier": 4.0, "display": "one-time refactor history"}
        if category == CATEGORY_STRUCTURAL:
            return {"include": True, "cost_multiplier": 2.5, "display": "lower-weight structural history"}
        return {"include": True, "cost_multiplier": 1.5, "display": "historical coupling"}

    raise ValueError(f"unknown co-change consumer mode: {mode}")


@dataclass(frozen=True)
class CoChangeConfig:
    min_occurrences: int = 3
    min_jaccard: float = 0.20
    shared_dependency_ratio: float = 0.70
    burst_days: int = 21


def pair_key(a: str, b: str) -> tuple[str, str]:
    if a == b:
        raise ValueError("co-change pair endpoints must differ")
    return (a, b) if a < b else (b, a)


def add_commit_pairs(pair_commits: dict[tuple[str, str], set[str]], changed_functions: Iterable[str], commit_sha: str) -> None:
    changed = sorted(set(changed_functions))
    for a, b in combinations(changed, 2):
        pair_commits[pair_key(a, b)].add(commit_sha)


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _span_days(commits: Iterable[str], commit_timestamps: dict[str, str]) -> int | None:
    dates = [
        parsed
        for commit in commits
        if (parsed := _parse_dt(commit_timestamps.get(commit, ""))) is not None
    ]
    if len(dates) < 2:
        return 0 if dates else None
    return max(0, (max(dates) - min(dates)).days)


def _bucket_float(value: float, step: float = 0.05) -> str:
    low = math.floor(value / step) * step
    high = low + step
    return f"{low:.2f}-{high:.2f}"


def _bucket_days(value: int | None) -> str:
    if value is None:
        return "unknown"
    for limit in (0, 7, 14, 21, 30, 60, 90, 180, 365):
        if value <= limit:
            return f"<= {limit}"
    return "> 365"


def _dependency_candidates(
    *,
    commits: list[str],
    source: str,
    target: str,
    function_files: dict[str, str],
    commit_changed_functions: dict[str, set[str]],
) -> tuple[str, int, float]:
    counts: Counter[str] = Counter()
    source_file = function_files.get(source, "")
    target_file = function_files.get(target, "")
    for commit in commits:
        functions = set(commit_changed_functions.get(commit, set()))
        files = {
            function_files.get(func, "")
            for func in functions
            if function_files.get(func, "")
        }
        for func in functions - {source, target}:
            counts[f"function:{func}"] += 1
        for file_path in files - {source_file, target_file, ""}:
            counts[f"file:{file_path}"] += 1
    if not counts:
        return "", 0, 0.0
    dependency_id, support = counts.most_common(1)[0]
    ratio = support / max(1, len(commits))
    return dependency_id, support, ratio


def _classify_pair(
    *,
    source: str,
    target: str,
    commits: list[str],
    function_files: dict[str, str],
    calls_pairs: set[tuple[str, str]],
    import_pairs: set[tuple[str, str]],
    commit_changed_functions: dict[str, set[str]],
    commit_timestamps: dict[str, str],
    config: CoChangeConfig,
) -> tuple[str, dict]:
    unordered = pair_key(source, target)
    source_file = function_files.get(source, "")
    target_file = function_files.get(target, "")
    file_pair = pair_key(source_file, target_file) if source_file and target_file and source_file != target_file else None

    if unordered in calls_pairs:
        return CATEGORY_STRUCTURAL, {
            "reason": "direct_call",
            "support": "CALLS",
        }
    if file_pair is not None and file_pair in import_pairs:
        return CATEGORY_STRUCTURAL, {
            "reason": "direct_import",
            "support": "IMPORTS",
        }

    dependency_id, support, ratio = _dependency_candidates(
        commits=commits,
        source=source,
        target=target,
        function_files=function_files,
        commit_changed_functions=commit_changed_functions,
    )
    if dependency_id and ratio >= config.shared_dependency_ratio:
        return CATEGORY_SHARED_DEP, {
            "reason": "shared_dependency",
            "dependency_id": dependency_id,
            "support_count": support,
            "support_ratio": round(ratio, 6),
        }

    days = _span_days(commits, commit_timestamps)
    if days is not None and days <= config.burst_days:
        return CATEGORY_BURST, {
            "reason": "temporal_burst",
            "span_days": days,
        }

    return CATEGORY_SHARED_ONLY, {
        "reason": "unexplained_shared_commits",
    }


def _theme_prompt(theme_id: str, category: str, pairs: list[dict], commit_messages: dict[str, str]) -> str:
    commits = sorted({commit for pair in pairs for commit in pair["source_commit_shas"]})
    messages = [f"- {commit}: {commit_messages.get(commit, '').splitlines()[0]}" for commit in commits[:30]]
    pair_lines = [f"- {pair['source']} <-> {pair['target']}" for pair in pairs[:30]]
    return "\n".join(
        [
            f"Theme: {theme_id}",
            f"Category: {category}",
            "Pairs:",
            *pair_lines,
            "Supporting commits:",
            *messages,
            "Question: Is there one shared reason these functions keep changing together, or no clear shared reason?",
        ]
    )


def _fallback_components(edges: list[tuple[str, str]]) -> list[set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        neighbors[a].add(b)
        neighbors[b].add(a)
    seen: set[str] = set()
    components: list[set[str]] = []
    for node in sorted(neighbors):
        if node in seen:
            continue
        stack = [node]
        component: set[str] = set()
        seen.add(node)
        while stack:
            current = stack.pop()
            component.add(current)
            for nxt in sorted(neighbors[current]):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        components.append(component)
    return components


def _shared_commit_components(pairs: list[dict]) -> list[list[dict]]:
    if not pairs:
        return []
    nodes = sorted({node for pair in pairs for node in (pair["source"], pair["target"])})
    node_to_idx = {node: idx for idx, node in enumerate(nodes)}
    edges = [(pair["source"], pair["target"]) for pair in pairs]
    try:
        import igraph as ig

        graph = ig.Graph(directed=False)
        graph.add_vertices(len(nodes))
        graph.add_edges([(node_to_idx[a], node_to_idx[b]) for a, b in edges])
        graph.es["weight"] = [max(pair["jaccard"], 0.0001) for pair in pairs]
        communities = graph.community_infomap(edge_weights=graph.es["weight"])
        node_cluster = {
            nodes[idx]: cluster_id
            for idx, cluster_id in enumerate(communities.membership)
        }
        by_cluster: dict[int, list[dict]] = defaultdict(list)
        for pair in pairs:
            cluster_id = min(node_cluster[pair["source"]], node_cluster[pair["target"]])
            by_cluster[cluster_id].append(pair)
        return [by_cluster[key] for key in sorted(by_cluster)]
    except Exception:
        components = _fallback_components(edges)
        component_id: dict[str, int] = {}
        for idx, component in enumerate(components):
            for node in component:
                component_id[node] = idx
        by_component: dict[int, list[dict]] = defaultdict(list)
        for pair in pairs:
            by_component[component_id[pair["source"]]].append(pair)
        return [by_component[key] for key in sorted(by_component)]


def _assign_themes(pairs: list[dict], commit_messages: dict[str, str]) -> tuple[list[dict], list[dict]]:
    themes: list[dict] = []
    themed_pairs = [dict(pair) for pair in pairs]
    commit_theme_ids: dict[str, set[str]] = defaultdict(set)

    shared_dep_groups: dict[str, list[dict]] = defaultdict(list)
    temporal_burst_groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    shared_only: list[dict] = []
    for pair in themed_pairs:
        if pair["category"] == CATEGORY_SHARED_DEP:
            dependency_id = pair.get("explanation", {}).get("dependency_id", "unknown")
            shared_dep_groups[dependency_id].append(pair)
        elif pair["category"] == CATEGORY_BURST:
            temporal_burst_groups[tuple(pair["source_commit_shas"])].append(pair)
        elif pair["category"] == CATEGORY_SHARED_ONLY:
            shared_only.append(pair)

    def register_theme(theme: dict) -> None:
        themes.append(theme)
        for commit in theme["source_commit_shas"]:
            commit_theme_ids[commit].add(theme["theme_id"])

    theme_index = 1
    for dependency_id in sorted(shared_dep_groups):
        group = shared_dep_groups[dependency_id]
        theme_id = f"cochange_theme_{theme_index:04d}"
        theme_index += 1
        register_theme(_theme_record(theme_id, CATEGORY_SHARED_DEP, group, commit_messages, dependency_id=dependency_id))

    for commit_signature in sorted(temporal_burst_groups):
        group = temporal_burst_groups[commit_signature]
        theme_id = f"cochange_theme_{theme_index:04d}"
        theme_index += 1
        register_theme(_theme_record(theme_id, CATEGORY_BURST, group, commit_messages))

    for group in _shared_commit_components(shared_only):
        theme_id = f"cochange_theme_{theme_index:04d}"
        theme_index += 1
        register_theme(_theme_record(theme_id, CATEGORY_SHARED_ONLY, group, commit_messages))

    theme_lookup = {theme["theme_id"]: theme for theme in themes}
    for pair in themed_pairs:
        theme_counts: Counter[str] = Counter()
        for commit in pair["source_commit_shas"]:
            theme_ids = sorted(commit_theme_ids.get(commit, set()))
            if not theme_ids:
                continue
            vote = 1.0 / len(theme_ids)
            for theme_id in theme_ids:
                theme_counts[theme_id] += vote

        occurrence = max(1, pair["occurrence_count"])
        ordered_counts = sorted(
            theme_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        pair["theme_counts"] = {
            theme_id: round(count, 6)
            for theme_id, count in ordered_counts
        }
        pair["theme_proportions"] = {
            theme_id: round(count / occurrence, 6)
            for theme_id, count in ordered_counts
        }
        pair["theme_labels"] = {
            theme_id: _theme_label(theme_lookup[theme_id])
            for theme_id, _count in ordered_counts
            if theme_id in theme_lookup
        }
        if ordered_counts:
            pair["theme_id"] = ordered_counts[0][0]
        else:
            pair["theme_id"] = ""

    return themed_pairs, themes


def _theme_label(theme: dict) -> str:
    if theme.get("dependency_id"):
        return theme["dependency_id"]
    commits = theme.get("source_commit_shas") or []
    if theme.get("category") == CATEGORY_BURST and commits:
        return f"temporal burst: {commits[0]}..{commits[-1]}"
    return theme["category"]


def _theme_record(
    theme_id: str,
    category: str,
    pairs: list[dict],
    commit_messages: dict[str, str],
    *,
    dependency_id: str = "",
) -> dict:
    function_ids = sorted({node for pair in pairs for node in (pair["source"], pair["target"])})
    commit_shas = sorted({commit for pair in pairs for commit in pair["source_commit_shas"]})
    return {
        "theme_id": theme_id,
        "category": category,
        "dependency_id": dependency_id,
        "pair_count": len(pairs),
        "function_ids": function_ids,
        "source_commit_shas": commit_shas,
        "commit_messages": [commit_messages.get(commit, "") for commit in commit_shas],
        "ai_prompt": _theme_prompt(theme_id, category, pairs, commit_messages),
        "ai_summary": None,
    }


def _histograms(all_candidates: list[dict], survivors: list[dict]) -> dict:
    return {
        "candidate_count": len(all_candidates),
        "survivor_count": len(survivors),
        "occurrence_count": dict(sorted(Counter(row["occurrence_count"] for row in all_candidates).items())),
        "jaccard": dict(sorted(Counter(_bucket_float(row["jaccard"]) for row in all_candidates).items())),
        "third_node_overlap": dict(sorted(Counter(_bucket_float(row["third_node_overlap_ratio"]) for row in survivors).items())),
        "span_days": dict(sorted(Counter(_bucket_days(row["span_days"]) for row in survivors).items())),
        "category": dict(sorted(Counter(row["category"] for row in survivors).items())),
    }


def analyze_cochanges(
    *,
    pair_commits: dict[tuple[str, str], set[str]],
    function_touch_commits: dict[str, set[str]],
    function_files: dict[str, str],
    calls_pairs: set[tuple[str, str]],
    import_pairs: set[tuple[str, str]],
    commit_changed_functions: dict[str, set[str]],
    commit_timestamps: dict[str, str],
    commit_messages: dict[str, str],
    config: CoChangeConfig | None = None,
) -> dict:
    config = config or CoChangeConfig()
    all_candidates: list[dict] = []
    survivors: list[dict] = []

    for source, target in sorted(pair_commits):
        commits = sorted(pair_commits[(source, target)])
        occurrence = len(commits)
        source_touches = len(function_touch_commits.get(source, set()))
        target_touches = len(function_touch_commits.get(target, set()))
        denominator = source_touches + target_touches - occurrence
        jaccard = occurrence / denominator if denominator > 0 else 0.0
        span = _span_days(commits, commit_timestamps)
        dependency_id, dependency_support, dependency_ratio = _dependency_candidates(
            commits=commits,
            source=source,
            target=target,
            function_files=function_files,
            commit_changed_functions=commit_changed_functions,
        )
        candidate = {
            "source": source,
            "target": target,
            "occurrence_count": occurrence,
            "co_change_count": occurrence,
            "touch_count_source": source_touches,
            "touch_count_target": target_touches,
            "jaccard": round(jaccard, 6),
            "source_commit_shas": commits,
            "span_days": span,
            "third_node_overlap_id": dependency_id,
            "third_node_overlap_count": dependency_support,
            "third_node_overlap_ratio": round(dependency_ratio, 6),
        }
        all_candidates.append(candidate)
        if occurrence < config.min_occurrences or jaccard < config.min_jaccard:
            continue

        category, explanation = _classify_pair(
            source=source,
            target=target,
            commits=commits,
            function_files=function_files,
            calls_pairs=calls_pairs,
            import_pairs=import_pairs,
            commit_changed_functions=commit_changed_functions,
            commit_timestamps=commit_timestamps,
            config=config,
        )
        row = {
            **candidate,
            "category": category,
            "theme_id": "",
            "explanation": explanation,
        }
        survivors.append(row)

    themed_pairs, themes = _assign_themes(survivors, commit_messages)

    edges = []
    for pair in themed_pairs:
        edge = {
            "source": pair["source"],
            "target": pair["target"],
            "type": "CO_CHANGE",
            "occurrence_count": pair["occurrence_count"],
            "co_change_count": pair["co_change_count"],
            "jaccard": pair["jaccard"],
            "touch_count_source": pair["touch_count_source"],
            "touch_count_target": pair["touch_count_target"],
            "source_commit_shas": pair["source_commit_shas"],
            "category": pair["category"],
            "theme_id": pair.get("theme_id", ""),
            "theme_counts": pair.get("theme_counts", {}),
            "theme_proportions": pair.get("theme_proportions", {}),
            "theme_labels": pair.get("theme_labels", {}),
            "ast_relation_type": "co_change",
        }
        edges.append(edge)

    threshold_report = {
        "config": {
            "min_occurrences": config.min_occurrences,
            "min_jaccard": config.min_jaccard,
            "shared_dependency_ratio": config.shared_dependency_ratio,
            "burst_days": config.burst_days,
        },
        "histograms": _histograms(all_candidates, themed_pairs),
    }

    return {
        "edges": edges,
        "pairs": themed_pairs,
        "themes": themes,
        "threshold_report": threshold_report,
    }
