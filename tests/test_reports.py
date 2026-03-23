from __future__ import annotations

from durep.analytics import (
    build_drilldown_tree,
    compute_all_uncompressed_stats,
    compute_directory_deltas,
)
from durep.ncdu import NcduDir
from durep.reports import drilldown_to_d3

from test_analytics import make_dir, make_file


def d3_leaf_sum(node: dict) -> int:
    """Replicate D3's hierarchy.sum(): recursively sum leaf 'value' fields."""
    if "children" in node:
        return sum(d3_leaf_sum(c) for c in node["children"])
    return node.get("value", 0)


def test_d3_leaf_sum_matches_total_bytes_with_dir_overhead() -> None:
    root = make_dir(
        "/data",
        None,
        lambda r: [
            make_file(r, "b.txt", 2000),
            make_dir("sub", r, lambda s: [make_file(s, "a.txt", 1000)], disk_size=512),
        ],
        disk_size=512,
    )

    uncompressed = compute_all_uncompressed_stats(root)
    drilldown = build_drilldown_tree(root, uncompressed, top_n=25, max_depth=100)
    d3 = drilldown_to_d3(drilldown)

    assert d3_leaf_sum(d3) == root.total_bytes


def test_d3_leaf_sum_matches_total_bytes_without_dir_overhead() -> None:
    root = make_dir(
        "/data",
        None,
        lambda r: [make_file(r, "a.txt", 500), make_file(r, "b.txt", 300)],
        disk_size=0,
    )

    uncompressed = compute_all_uncompressed_stats(root)
    drilldown = build_drilldown_tree(root, uncompressed, top_n=25, max_depth=100)
    d3 = drilldown_to_d3(drilldown)

    assert d3_leaf_sum(d3) == root.total_bytes


def test_d3_previous_bytes_matches_when_unchanged() -> None:
    def make_tree() -> NcduDir:
        return make_dir(
            "/data",
            None,
            lambda r: [
                make_file(r, "b.txt", 2000),
                make_dir("sub", r, lambda s: [make_file(s, "a.txt", 1000)], disk_size=512),
            ],
            disk_size=512,
        )

    current = make_tree()
    previous = make_tree()
    deltas = compute_directory_deltas(current, previous)
    uncompressed = compute_all_uncompressed_stats(current)
    drilldown = build_drilldown_tree(current, uncompressed, top_n=25, max_depth=100, deltas=deltas)
    d3 = drilldown_to_d3(drilldown)

    def check_all_deltas_zero(node: dict) -> None:
        prev = node.get("previousBytes")
        if prev is not None:
            assert d3_leaf_sum(node) == prev, f"delta mismatch on {node['name']}"
        for child in node.get("children", []):
            check_all_deltas_zero(child)

    check_all_deltas_zero(d3)
