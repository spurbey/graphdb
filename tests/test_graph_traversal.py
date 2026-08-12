from __future__ import annotations

from graph_traversal import (
    TraversalEdge,
    build_overlay_edges,
    connect_seeds,
)


def test_affinity_dimension_is_specific_and_direct_structural_edge_has_floor():
    edges, audit = build_overlay_edges(
        [
            {"source": "a", "target": "b", "label": "CALLS"},
        ],
        [
            {
                "source": "a",
                "target": "b",
                "payload": {
                    "global": 0.9,
                    "domains": {"auth": 0.2},
                    "change_kinds": {"bug_fix": 0.8},
                    "episode_count": 3,
                },
            }
        ],
        domain="auth",
        change_kind="bug_fix",
    )

    assert audit["affinity_overlay_count"] == 1
    assert len(edges) == 1
    assert edges[0].label == "CALLS"
    assert edges[0].affinity_score == 0.2
    assert edges[0].cost == 0.95


def test_shortcut_requires_two_episodes_and_is_bounded():
    edges, audit = build_overlay_edges(
        [],
        [
            {
                "source": "a",
                "target": "b",
                "payload": {
                    "global": 1.0,
                    "episode_count": 1,
                },
            },
            {
                "source": "a",
                "target": "c",
                "payload": {
                    "global": 1.0,
                    "episode_count": 2,
                },
            },
        ],
    )
    assert audit["affinity_shortcut_count"] == 1
    assert {(edge.source, edge.target) for edge in edges} == {("a", "c")}
    assert edges[0].cost == 1.25


def test_shared_prefix_union_beats_independent_paths():
    structural = [
        TraversalEdge("s1", "shared", "CALLS", 1.0, True),
        TraversalEdge("s2", "shared", "CALLS", 1.0, True),
        TraversalEdge("s1", "private1", "CALLS", 1.0, True),
        TraversalEdge("private1", "root", "CALLS", 1.0, True),
        TraversalEdge("s2", "private2", "CALLS", 1.0, True),
        TraversalEdge("private2", "root", "CALLS", 1.0, True),
        TraversalEdge("shared", "root", "CALLS", 1.0, True),
    ]

    result = connect_seeds(
        ["s1", "s2"], structural, root_ids=["root"], max_depth=3
    )

    assert result["connected"] is True
    assert result["root"] == "root"
    assert [row["nodes"] for row in result["paths"]] == [
        ["s1", "shared", "root"],
        ["s2", "shared", "root"],
    ]
    assert result["union_edge_count"] == 3
    assert result["union_cost"] == 3.0


def test_overlay_can_choose_repeated_learned_shortcut_but_not_direct_structural_bypass():
    structural = [
        TraversalEdge("s1", "x", "CALLS", 1.0, True),
        TraversalEdge("s2", "x", "CALLS", 1.0, True),
        TraversalEdge("x", "root", "CALLS", 1.0, True),
    ]
    overlay, _ = build_overlay_edges(
        [edge.__dict__ for edge in structural],
        [
            {
                "source": "s1",
                "target": "root",
                "payload": {"global": 1.0, "episode_count": 3},
            },
            {
                "source": "s2",
                "target": "root",
                "payload": {"global": 1.0, "episode_count": 3},
            },
        ],
    )

    result = connect_seeds(
        ["s1", "s2"], structural + overlay, root_ids=["root"], max_depth=3
    )

    assert result["connected"] is True
    assert any(row["label"] == "WORK_AFFINITY" for row in result["union_edges"])
    assert all(
        row["cost"] >= 0.75
        for row in result["union_edges"]
        if row["label"] == "CALLS"
    )


def test_undirected_reverse_traversal_keeps_physical_edge_cost():
    edges = [TraversalEdge("a", "b", "CALLS", 1.0, True)]

    result = connect_seeds(
        ["b", "a"], edges, root_ids=["a"], mode="all", max_depth=2
    )

    assert result["union_cost"] == 1.0
    assert result["union_edges"] == [
        {
            "source": "a",
            "target": "b",
            "label": "CALLS",
            "cost": 1.0,
            "structural": True,
            "affinity_score": 0.0,
            "episodes": 0,
        }
    ]
