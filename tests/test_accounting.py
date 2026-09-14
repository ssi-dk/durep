from __future__ import annotations

from pathlib import Path

import pytest
from test_ncdu import write_ncdu_json
from test_reports import d3_leaf_sum

from durep.analytics import build_drilldown_tree, compute_directory_deltas, compute_global_metrics
from durep.ncdu import CollapsedNode, parse_ncdu_json_file
from durep.reports import drilldown_to_d3, render_text_report


@pytest.mark.parametrize("budget", [1, 5, 1000])
@pytest.mark.parametrize("top_n", [1, 20])
def test_directory_accounting_survives_display_pruning(
    tmp_path: Path, budget: int, top_n: int
) -> None:
    path = tmp_path / "scan.json"
    write_ncdu_json(
        path,
        [
            {"name": "/data", "dsize": 4096},
            {"name": "a", "dsize": 200},
            {"name": "b", "dsize": 100},
            [
                {"name": "sub", "dsize": 512},
                {"name": "c", "dsize": 1000},
                [{"name": "nested", "dsize": 256}, {"name": "d", "dsize": 2000}],
            ],
            [{"name": "sibling", "dsize": 128}, {"name": "e", "dsize": 3000}],
        ],
    )
    run = parse_ncdu_json_file(path, top_n=top_n, display_nodes=budget)
    assert {key: value.direct_bytes for key, value in run.directories.items()} == {
        "/data": 4396,
        "/data/sub": 1512,
        "/data/sub/nested": 2256,
        "/data/sibling": 3128,
    }
    assert sum(d.direct_bytes for d in run.directories.values()) == run.root.total_bytes

    reference = parse_ncdu_json_file(path, top_n=100, display_nodes=1000)
    assert run.directories == reference.directories
    deltas = compute_directory_deltas(run, reference)
    assert all(
        d.delta_bytes == 0 and d.direct_delta_bytes == 0
        for p, d in deltas.items()
        if p in run.directories
    )
    report = render_text_report(run, reference, compute_global_metrics(run.root), deltas, 20)
    reference_report = render_text_report(
        reference,
        reference,
        compute_global_metrics(reference.root),
        compute_directory_deltas(reference, reference),
        20,
    )
    assert report.split("Top ", 1)[1] == reference_report.split("Top ", 1)[1]
    assert "growing directories" not in report
    assert "shrinking directories" not in report


@pytest.mark.parametrize("budget", [1, 1000])
def test_added_deleted_and_replaced_directories_have_their_own_direct_changes(
    tmp_path: Path, budget: int
) -> None:
    previous_path = tmp_path / "previous.json"
    current_path = tmp_path / "current.json"
    write_ncdu_json(
        previous_path,
        [
            {"name": "/data", "dsize": 4096},
            {"name": "direct", "dsize": 200},
            [{"name": "deleted", "dsize": 512}, {"name": "f", "dsize": 1000}],
            [{"name": "changed"}, {"name": "f", "dsize": 100}],
            [{"name": "now-file"}, {"name": "f", "dsize": 300}],
            {"name": "now-dir", "dsize": 50},
        ],
    )
    write_ncdu_json(
        current_path,
        [
            {"name": "/data", "dsize": 4096},
            {"name": "direct", "dsize": 200},
            [
                {"name": "added", "dsize": 256},
                [{"name": "nested", "dsize": 128}, {"name": "f", "dsize": 2000}],
            ],
            [{"name": "changed"}, {"name": "f", "dsize": 150}],
            {"name": "now-file", "dsize": 75},
            [{"name": "now-dir"}, {"name": "f", "dsize": 80}],
        ],
    )
    previous = parse_ncdu_json_file(previous_path, top_n=1, display_nodes=budget)
    current = parse_ncdu_json_file(current_path, top_n=1, display_nodes=budget)
    deltas = compute_directory_deltas(current, previous)
    direct = {
        p: d.direct_delta_bytes for p, d in deltas.items() if d.direct_delta_bytes is not None
    }
    assert direct == {
        "/data": 25,  # The replacement files belong directly to /data.
        "/data/deleted": -1512,
        "/data/added": 256,
        "/data/added/nested": 2128,
        "/data/changed": 50,
        "/data/now-file": -300,
        "/data/now-dir": 80,
    }
    assert sum(direct.values()) == current.root.total_bytes - previous.root.total_bytes
    report = render_text_report(current, previous, compute_global_metrics(current.root), deltas, 20)
    assert "+2.128 KB" in report
    assert "-1.512 KB" in report
    assert "total  /data/added/nested" in report
    assert "total  /data/deleted" in report


@pytest.mark.parametrize("current_b", [90, 110])
def test_other_has_no_delta_with_overhead_or_changing_membership(
    tmp_path: Path, current_b: int
) -> None:
    path = tmp_path / "scan.json"
    runs = []
    for size in [90, current_b]:
        write_ncdu_json(
            path,
            [
                {"name": "/data", "dsize": 4096},
                {"name": "a", "dsize": 100},
                {"name": "b", "dsize": size},
            ],
        )
        runs.append(parse_ncdu_json_file(path, top_n=1, display_nodes=1000))
    previous, current = runs
    deltas = compute_directory_deltas(current, previous)
    for top_n in [1, 20]:
        tree = build_drilldown_tree(current.root, top_n, deltas)
        data = drilldown_to_d3(tree)
        group = next(
            child for child in data["children"] if child["name"] == tree.children[-1].path.name
        )
        assert "previousBytes" not in group
        assert group["value"] == min(100, current_b)
        assert d3_leaf_sum(data) == current.root.total_bytes
        assert data["previousBytes"] == previous.root.total_bytes


def test_named_collapsed_directories_keep_deltas_and_groups_cannot_match_real_names(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scan.json"
    runs = []
    for size in [100, 200]:
        write_ncdu_json(
            path,
            [
                {"name": "/data"},
                [{"name": "(1 collapsed entries)"}, {"name": "f", "dsize": size}],
                {"name": "a", "dsize": 20},
                {"name": "b", "dsize": 10},
            ],
        )
        runs.append(parse_ncdu_json_file(path, top_n=1, display_nodes=1))
    previous, current = runs
    assert isinstance(current.root.children[0], CollapsedNode)
    deltas = compute_directory_deltas(current, previous)
    tree = build_drilldown_tree(current.root, 20, deltas)
    directory, file, group = tree.children
    assert directory.previous_bytes == 100
    assert directory.delta_bytes == 100
    assert file.previous_bytes == 20
    assert group.previous_bytes is None
    assert group.delta_bytes is None


@pytest.mark.parametrize("reverse", [False, True])
def test_directory_addition_or_deletion_does_not_change_parent_self_usage(
    tmp_path: Path, reverse: bool
) -> None:
    path = tmp_path / "scan.json"
    root: list[object] = [{"name": "/data"}]
    write_ncdu_json(path, root)
    empty = parse_ncdu_json_file(path, top_n=1, display_nodes=1)
    write_ncdu_json(path, root + [[{"name": "new"}, {"name": "file", "dsize": 1000}]])
    populated = parse_ncdu_json_file(path, top_n=1, display_nodes=1)
    current, previous = (empty, populated) if reverse else (populated, empty)
    deltas = compute_directory_deltas(current, previous)
    assert deltas["/data"].direct_delta_bytes == 0
    assert deltas["/data/new"].direct_delta_bytes == (-1000 if reverse else 1000)
