from __future__ import annotations

from helix_traversal import HelixNeighborhoodReader


class FakeReader(HelixNeighborhoodReader):
    def __init__(self):
        pass

    def _neighbor_query(self, node_id, *, labels, direction, limit):
        graph = {
            ("a", "out", "CALLS"): [{"node_id": "b"}, {"node_id": "c"}],
            ("b", "out", "CALLS"): [{"node_id": "d"}],
            ("c", "out", "CALLS"): [{"node_id": "d"}],
        }
        return {
            label: graph.get((node_id, direction, label), []) for label in labels
        }

    def _affinity_query(self, source_id, limit):
        if source_id == "a":
            return [
                {
                    "source": "a",
                    "target": "d",
                    "payload": {"global": 0.8, "episode_count": 2},
                }
            ]
        return []


def test_bounded_reader_loads_frontiers_without_full_graph():
    neighborhood = FakeReader().load(
        ["a"],
        depth=2,
        direction="out",
        structural_labels=("CALLS",),
        node_budget=10,
        edge_budget=10,
    )

    assert set(neighborhood.nodes) == {"a", "b", "c", "d"}
    assert len(neighborhood.structural_edges) == 4
    assert len(neighborhood.affinity_edges) == 1
    assert neighborhood.truncated is False
    # The affinity shortcut discovers d at level one, so it is also expanded
    # within the requested depth budget.
    assert neighborhood.queries == 8


def test_bounded_reader_discloses_budget_truncation():
    neighborhood = FakeReader().load(
        ["a"],
        depth=3,
        direction="out",
        structural_labels=("CALLS",),
        node_budget=2,
        edge_budget=10,
    )

    assert len(neighborhood.nodes) == 2
    assert neighborhood.truncated is True
