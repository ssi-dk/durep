from __future__ import annotations

import datetime

from durep.analytics import (
    ProjectSample,
    ProjectTimeSeries,
    build_drilldown_tree,
    compute_directory_deltas,
)
from durep.metadata import Owner, ProjectName
from durep.ncdu import NcduDir, UncompressedStats
from durep.reports import drilldown_to_d3, render_overview_html_report, render_overview_text_report

from test_analytics import make_dir, make_file, make_sample

PN = ProjectName
OW = Owner


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

    drilldown = build_drilldown_tree(root, top_n=25)
    d3 = drilldown_to_d3(drilldown)

    assert d3_leaf_sum(d3) == root.total_bytes


def test_d3_leaf_sum_matches_total_bytes_without_dir_overhead() -> None:
    root = make_dir(
        "/data",
        None,
        lambda r: [make_file(r, "a.txt", 500), make_file(r, "b.txt", 300)],
        disk_size=0,
    )

    drilldown = build_drilldown_tree(root, top_n=25)
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
    drilldown = build_drilldown_tree(current, top_n=25, deltas=deltas)
    d3 = drilldown_to_d3(drilldown)

    def check_all_deltas_zero(node: dict) -> None:
        prev = node.get("previousBytes")
        if prev is not None:
            assert d3_leaf_sum(node) == prev, f"delta mismatch on {node['name']}"
        for child in node.get("children", []):
            check_all_deltas_zero(child)

    check_all_deltas_zero(d3)


def test_d3_includes_compressible_ratio_for_directories() -> None:
    root = make_dir(
        "/data",
        None,
        lambda r: [
            make_file(r, "reads.fastq", 400),
            make_dir("nested", r, lambda s: [make_file(s, "other.bin", 600)]),
        ],
    )

    drilldown = build_drilldown_tree(root, top_n=25)
    d3 = drilldown_to_d3(drilldown)

    assert d3["compressibleRatio"] == 0.4


def test_render_overview_text_report_caps_extreme_growth_percentage() -> None:
    samples = [
        make_sample("/proj", datetime.date(2024, 1, 1), 1),
        make_sample("/proj", datetime.date(2024, 1, 2), 10_002),
    ]
    series = [
        ProjectTimeSeries(
            project="/proj",
            dates=[datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)],
            bytes_values=[1, 10_002],
            uncompressed_values=[UncompressedStats.zero(), UncompressedStats.zero()],
            measured=[True, True],
        )
    ]

    report = render_overview_text_report(series, samples, {PN("/proj"): OW("/proj")})

    assert "> 1000%" in report
    assert "+1000100.0%" not in report


def test_render_overview_html_report_shows_total_files_card() -> None:
    samples = [
        ProjectSample(
            project="proj_a",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100,
            total_files=3,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
        ProjectSample(
            project="proj_b",
            timestamp=datetime.datetime(2024, 1, 2, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 2),
            total_bytes=200,
            total_files=4,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
    ]
    series = [
        ProjectTimeSeries(
            project="proj_a",
            dates=[datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)],
            bytes_values=[100, 100],
            uncompressed_values=[UncompressedStats.zero(), UncompressedStats.zero()],
            measured=[True, False],
        ),
        ProjectTimeSeries(
            project="proj_b",
            dates=[datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)],
            bytes_values=[200, 200],
            uncompressed_values=[UncompressedStats.zero(), UncompressedStats.zero()],
            measured=[False, True],
        ),
    ]
    owners = {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("Bob")}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert "Total files" in html
    assert '"files": [3, 4]' in html


def test_render_overview_html_report_includes_project_history_tooltip_logic() -> None:
    samples = [
        ProjectSample(
            project="proj_a",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100,
            total_files=3,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        )
    ]
    series = [
        ProjectTimeSeries(
            project="proj_a",
            dates=[datetime.date(2024, 1, 1), datetime.date(2024, 1, 2)],
            bytes_values=[100, 100],
            uncompressed_values=[UncompressedStats.zero(), UncompressedStats.zero()],
            measured=[True, False],
        )
    ]
    owners = {PN("proj_a"): OW("Alice")}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert "projectHistoryHtml" in html
    assert "Observed sizes" in html
    assert '<strong>" + project + "</strong><br>' in html


def test_render_overview_html_report_includes_scrollable_html_legend() -> None:
    samples = [
        ProjectSample(
            project=f"proj_{i}",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100 + i,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        )
        for i in range(30)
    ]
    series = [
        ProjectTimeSeries(
            project=f"proj_{i}",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[100 + i],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        )
        for i in range(30)
    ]
    owners = {PN(f"proj_{i}"): OW(f"owner_{i % 5}") for i in range(30)}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert 'class="overview-legend"' in html
    assert 'id="overview-legend"' in html
    assert "max-height:400px" in html or "max-height: 400px" in html
    assert "overflow-y:auto" in html or "overflow-y: auto" in html
    assert "renderStackedArea('overview-chart', 'overview-legend'" in html


def test_render_overview_html_report_sorts_stack_by_latest_size() -> None:
    samples = [
        ProjectSample(
            project="proj_a",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
        ProjectSample(
            project="proj_b",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=200,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
    ]
    series = [
        ProjectTimeSeries(
            project="proj_a",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[100],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        ),
        ProjectTimeSeries(
            project="proj_b",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[200],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        ),
    ]
    owners = {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("Bob")}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert "latestSize" in html
    assert "b.latestSize - a.latestSize" in html
    assert "absGrowth" not in html


def test_render_overview_html_report_includes_right_y_axis() -> None:
    samples = [
        ProjectSample(
            project="proj_a",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        )
    ]
    series = [
        ProjectTimeSeries(
            project="proj_a",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[100],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        )
    ]
    owners = {PN("proj_a"): OW("Alice")}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert 'const yAxisRight = svg.append("g")' in html
    assert "d3.axisRight(y)" in html


def test_render_overview_html_report_includes_owner_grouping() -> None:
    samples = [
        ProjectSample(
            project="proj_a",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=100,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
        ProjectSample(
            project="proj_b",
            timestamp=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
            date=datetime.date(2024, 1, 1),
            total_bytes=200,
            total_files=2,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        ),
    ]
    series = [
        ProjectTimeSeries(
            project="proj_a",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[100],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        ),
        ProjectTimeSeries(
            project="proj_b",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[200],
            uncompressed_values=[UncompressedStats.zero()],
            measured=[True],
        ),
    ]
    owners = {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("Alice")}

    html = render_overview_html_report(
        series, render_overview_text_report(series, samples, owners), samples, owners
    )

    assert "owner-group" in html
    assert "owner-header" in html
    assert '"owners"' in html
    assert '"Alice"' in html
