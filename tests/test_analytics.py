from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from durep.analytics import (
    ProjectSample,
    build_drilldown_tree,
    build_growth_drilldown,
    build_overview_series,
    build_shrinkage_drilldown,
    compute_directory_deltas,
    compute_global_metrics,
)
from durep.ncdu import CollapsedNode, NcduDir, NcduFile, NcduRun, UncompressedStats


def make_file(parent: NcduDir, name: str, disk_size: int) -> NcduFile:
    return NcduFile(
        basename=name,
        parent=parent,
        disk_size=disk_size,
    )


def make_dir(
    basename: str,
    parent: NcduDir | None,
    children_fn: Callable[[NcduDir], Iterable[NcduDir | NcduFile]] | None = None,
    disk_size: int = 0,
) -> NcduDir:
    """Create a directory node.

    Pass children_fn as a callable that receives the new dir node and returns its children,
    so that children can reference their parent. Or pass None for a leaf directory.
    """
    node = NcduDir(
        basename=basename,
        parent=parent,
        disk_size=disk_size,
        total_bytes=0,
        total_files=0,
        total_directories=0,
        uncompressed=UncompressedStats.zero(),
    )
    if children_fn is not None:
        node.children = list(children_fn(node))
    node.total_bytes = disk_size + sum(c.total_bytes for c in node.children)
    node.total_files = sum(c.total_files if isinstance(c, NcduDir) else 1 for c in node.children)
    node.total_directories = 1 + sum(
        c.total_directories for c in node.children if isinstance(c, NcduDir)
    )
    agg = UncompressedStats.zero()
    for child in node.children:
        if isinstance(child, CollapsedNode):
            agg.add_to_self(UncompressedStats.from_total_size(child.uncompressed_bytes))
        else:
            agg.add_to_self(child.uncompressed)
    node.uncompressed = agg
    return node


def build_bio_tree() -> NcduDir:
    """Tree with known bioinformatics files for uncompressed stats testing.

    /data
    ├── reads.fastq   (1000)
    ├── genome.fasta   (500)
    ├── align.sam      (2000)
    ├── variants.vcf   (300)
    ├── report.txt     (50)
    └── sub/
        ├── extra.fq   (400)
        └── other.bin  (100)
    """
    return make_dir(
        "/data",
        None,
        lambda root: [
            make_file(root, "reads.fastq", 1000),
            make_file(root, "genome.fasta", 500),
            make_file(root, "align.sam", 2000),
            make_file(root, "variants.vcf", 300),
            make_file(root, "report.txt", 50),
            make_dir(
                "sub",
                root,
                lambda sub: [
                    make_file(sub, "extra.fq", 400),
                    make_file(sub, "other.bin", 100),
                ],
            ),
        ],
    )


# --- uncompressed stats (computed during tree construction) ---


def test_uncompressed_stats_accumulates_bioinformatics_formats() -> None:
    root = build_bio_tree()

    assert root.uncompressed.fasta == 500
    assert root.uncompressed.fastq == 1000 + 400  # reads.fastq + extra.fq
    assert root.uncompressed.sam == 2000
    assert root.uncompressed.vcf == 300


def test_uncompressed_stats_are_zero_for_non_bio_files() -> None:
    root = make_dir("/plain", None, lambda r: [make_file(r, "notes.txt", 999)])

    assert root.uncompressed == UncompressedStats(0, 0, 0, 0, 0)


def test_uncompressed_stats_ignore_compressed_bio_files() -> None:
    root = make_dir(
        "/data",
        None,
        lambda r: [
            make_file(r, "reads.fastq.gz", 800),
            make_file(r, "genome.fasta.gz", 400),
            make_file(r, "align.sam.bz2", 600),
            make_file(r, "variants.vcf.gz", 200),
            make_file(r, "raw.fastq", 1000),  # this one IS uncompressed
        ],
    )

    # Only raw.fastq should count; the .gz/.bz2 files should not
    assert root.uncompressed.fastq == 1000
    assert root.uncompressed.fasta == 0
    assert root.uncompressed.sam == 0
    assert root.uncompressed.vcf == 0


def test_uncompressed_stats_per_subdirectory() -> None:
    root = build_bio_tree()

    sub = next(c for c in root.children if isinstance(c, NcduDir))
    assert sub.uncompressed.fastq == 400
    assert sub.uncompressed.fasta == 0
    assert sub.uncompressed.sam == 0
    assert sub.uncompressed.vcf == 0


# --- compute_global_metrics ---


def test_global_metrics_match_root_node() -> None:
    root = build_bio_tree()
    metrics = compute_global_metrics(root)

    assert metrics.total_usage_bytes == root.total_bytes
    assert metrics.total_files == root.total_files
    assert metrics.total_uncompressed == root.uncompressed


# --- compute_directory_deltas ---


def test_deltas_detect_growth() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 100)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 300)])

    deltas = compute_directory_deltas(curr_root, prev_root)
    assert "/data" in deltas
    assert deltas["/data"].delta_bytes == 200


def test_deltas_detect_shrinkage() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 500)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 200)])

    deltas = compute_directory_deltas(curr_root, prev_root)
    assert deltas["/data"].delta_bytes == -300


def test_deltas_only_include_paths_present_in_both_snapshots() -> None:
    prev_root = make_dir(
        "/data",
        None,
        lambda r: [
            make_dir("old", r, lambda d: [make_file(d, "x.txt", 10)]),
        ],
    )
    curr_root = make_dir(
        "/data",
        None,
        lambda r: [
            make_dir("new", r, lambda d: [make_file(d, "y.txt", 20)]),
        ],
    )

    deltas = compute_directory_deltas(curr_root, prev_root)
    # Root is in both, but /data/old and /data/new are not shared
    assert "/data" in deltas
    assert "/data/old" not in deltas
    assert "/data/new" not in deltas


# --- build_drilldown_tree ---


def build_wide_tree(n_children: int) -> NcduDir:
    """Directory with n_children files of decreasing size."""
    return make_dir(
        "/wide",
        None,
        lambda r: [make_file(r, f"f{i}.dat", (n_children - i) * 100) for i in range(n_children)],
    )


def test_drilldown_prunes_to_top_n() -> None:
    root = build_wide_tree(10)
    drilldown = build_drilldown_tree(root, top_n=3)

    # 3 kept + 1 "Other" node
    assert len(drilldown.children) == 4
    other = drilldown.children[-1]
    assert "Other" in str(other.path)
    assert "7 items" in str(other.path)


def test_drilldown_keeps_all_when_fewer_than_top_n() -> None:
    root = build_wide_tree(3)
    drilldown = build_drilldown_tree(root, top_n=10)

    assert len(drilldown.children) == 3
    assert all("Other" not in str(c.path) for c in drilldown.children)


def test_drilldown_other_node_bytes_equal_remainder_sum() -> None:
    root = build_wide_tree(6)
    drilldown = build_drilldown_tree(root, top_n=2)

    other = drilldown.children[-1]
    # Kept: f0 (600), f1 (500). Remainder: f2 (400) + f3 (300) + f4 (200) + f5 (100) = 1000
    assert other.total_bytes == 1000


# --- build_growth_drilldown / build_shrinkage_drilldown ---


def test_growth_drilldown_returns_none_when_nothing_grew() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 500)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 200)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_growth_drilldown(curr_root, deltas, top_n=10)
    assert result is None


def test_growth_drilldown_captures_positive_deltas() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 100)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 400)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_growth_drilldown(curr_root, deltas, top_n=10)
    assert result is not None
    assert result.total_bytes == 300


def test_shrinkage_drilldown_captures_negative_deltas() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 500)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 200)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_shrinkage_drilldown(curr_root, deltas, top_n=10)
    assert result is not None
    assert result.total_bytes == 300


def test_shrinkage_drilldown_returns_none_when_nothing_shrunk() -> None:
    prev_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 100)])
    curr_root = make_dir("/data", None, lambda r: [make_file(r, "a.txt", 400)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_shrinkage_drilldown(curr_root, deltas, top_n=10)
    assert result is None


# --- NcduRun.to_project_sample ---


def make_run(
    root_name: str = "/data", dsize: int = 100, timestamp_epoch: int = 1700000000
) -> NcduRun:
    root = make_dir(root_name, None, lambda r: [make_file(r, "a.txt", dsize)])
    ts = datetime.datetime.fromtimestamp(timestamp_epoch, tz=datetime.timezone.utc)
    return NcduRun(root=root, timestamp=ts)


def test_to_project_sample_basic() -> None:
    run = make_run("/proj", dsize=500, timestamp_epoch=1700000000)
    sample = run.to_project_sample()

    assert sample.project == "proj"
    assert sample.date == datetime.date(2023, 11, 14)
    assert sample.total_bytes == 500
    assert sample.total_files == 1
    assert sample.total_directories == 1


def test_to_project_sample_includes_uncompressed() -> None:
    root = make_dir("/bio", None, lambda r: [make_file(r, "reads.fastq", 1000)])
    ts = datetime.datetime.fromtimestamp(1700000000, tz=datetime.timezone.utc)
    run = NcduRun(root=root, timestamp=ts)

    sample = run.to_project_sample()
    assert sample.uncompressed.fastq == 1000


# --- build_overview_series ---


def make_sample(
    project: str,
    date: datetime.date,
    total_bytes: int,
    hour: int = 0,
) -> ProjectSample:
    ts = datetime.datetime(date.year, date.month, date.day, hour, tzinfo=datetime.timezone.utc)
    return ProjectSample(
        project=project,
        timestamp=ts,
        date=date,
        total_bytes=total_bytes,
        total_files=1,
        total_directories=1,
        uncompressed=UncompressedStats.zero(),
    )


def test_overview_series_day_binning() -> None:
    samples = [
        make_sample("/a", datetime.date(2024, 1, 1), 100),
        make_sample("/a", datetime.date(2024, 1, 3), 300),
    ]
    series = build_overview_series(samples)

    assert len(series) == 1
    ts = series[0]
    assert ts.dates == [
        datetime.date(2024, 1, 1),
        datetime.date(2024, 1, 2),
        datetime.date(2024, 1, 3),
    ]
    # Linear interpolation: day 2 gets midpoint between day 1 and day 3
    assert ts.bytes_values == [100, 200, 300]
    # Only days 1 and 3 are actual measurements
    assert ts.measured == [True, False, True]


def test_overview_series_back_fills_before_first_measurement() -> None:
    samples = [
        make_sample("/early", datetime.date(2024, 1, 1), 100),
        make_sample("/late", datetime.date(2024, 1, 3), 200),
    ]
    series = build_overview_series(samples)

    # /early is before /late alphabetically
    early = next(s for s in series if s.project == "/early")
    late = next(s for s in series if s.project == "/late")

    # /late back-fills days before first measurement with first measured value
    assert late.bytes_values[0] == 200
    assert late.bytes_values[1] == 200
    assert late.bytes_values[2] == 200

    # /early should forward-fill into days 2 and 3
    assert early.bytes_values == [100, 100, 100]


def test_overview_series_same_day_dedup() -> None:
    # Later timestamp wins, regardless of input order
    samples = [
        make_sample("/a", datetime.date(2024, 1, 1), 200, hour=14),
        make_sample("/a", datetime.date(2024, 1, 1), 100, hour=8),
    ]
    series = build_overview_series(samples)

    assert len(series) == 1
    assert series[0].bytes_values == [200]


def test_overview_series_empty() -> None:
    assert build_overview_series([]) == []


def test_project_time_series_rejects_mismatched_lengths() -> None:
    import pytest

    from durep.analytics import ProjectTimeSeries

    with pytest.raises(ValueError, match="list lengths must match"):
        ProjectTimeSeries(
            project="/a",
            dates=[datetime.date(2024, 1, 1)],
            bytes_values=[100, 200],
            uncompressed_values=[],
            measured=[True],
        )
