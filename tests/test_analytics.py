from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path

from durep.analytics import (
    ProjectSample,
    UncompressedStats,
    build_drilldown_tree,
    build_growth_drilldown,
    build_overview_series,
    build_shrinkage_drilldown,
    compute_all_uncompressed_stats,
    compute_directory_deltas,
    compute_global_metrics,
    extract_project_sample,
)
from durep.ncdu import NcduDir, NcduEntry, NcduFile, NcduRun


def make_file(parent: Path, name: str, disk_size: int) -> NcduFile:
    return NcduFile(
        path=parent / name,
        disk_size=disk_size,
    )


def make_dir(path: Path, children: Sequence[NcduEntry], disk_size: int = 0) -> NcduDir:
    total_bytes = disk_size + sum(c.total_bytes for c in children)
    total_files = sum(c.total_files if isinstance(c, NcduDir) else 1 for c in children)
    total_dirs = 1 + sum(c.total_directories for c in children if isinstance(c, NcduDir))
    return NcduDir(
        path=path,
        disk_size=disk_size,
        total_bytes=total_bytes,
        total_files=total_files,
        total_directories=total_dirs,
        children=list(children),
    )


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
    root_path = Path("/data")
    sub_path = root_path / "sub"

    sub = make_dir(
        sub_path,
        [
            make_file(sub_path, "extra.fq", 400),
            make_file(sub_path, "other.bin", 100),
        ],
    )

    return make_dir(
        root_path,
        [
            make_file(root_path, "reads.fastq", 1000),
            make_file(root_path, "genome.fasta", 500),
            make_file(root_path, "align.sam", 2000),
            make_file(root_path, "variants.vcf", 300),
            make_file(root_path, "report.txt", 50),
            sub,
        ],
    )


# --- compute_all_uncompressed_stats ---


def test_uncompressed_stats_accumulates_bioinformatics_formats() -> None:
    root = build_bio_tree()
    stats = compute_all_uncompressed_stats(root)

    root_stats = stats[root.path]
    assert root_stats.fasta == 500
    assert root_stats.fastq == 1000 + 400  # reads.fastq + extra.fq
    assert root_stats.sam == 2000
    assert root_stats.vcf == 300


def test_uncompressed_stats_are_zero_for_non_bio_files() -> None:
    root = make_dir(Path("/plain"), [make_file(Path("/plain"), "notes.txt", 999)])
    stats = compute_all_uncompressed_stats(root)

    root_stats = stats[root.path]
    assert root_stats == UncompressedStats(0, 0, 0, 0)


def test_uncompressed_stats_ignore_compressed_bio_files() -> None:
    root_path = Path("/data")
    root = make_dir(
        root_path,
        [
            make_file(root_path, "reads.fastq.gz", 800),
            make_file(root_path, "genome.fasta.gz", 400),
            make_file(root_path, "align.sam.bz2", 600),
            make_file(root_path, "variants.vcf.gz", 200),
            make_file(root_path, "raw.fastq", 1000),  # this one IS uncompressed
        ],
    )
    stats = compute_all_uncompressed_stats(root)

    root_stats = stats[root.path]
    # Only raw.fastq should count; the .gz/.bz2 files should not
    assert root_stats.fastq == 1000
    assert root_stats.fasta == 0
    assert root_stats.sam == 0
    assert root_stats.vcf == 0


def test_uncompressed_stats_per_subdirectory() -> None:
    root = build_bio_tree()
    stats = compute_all_uncompressed_stats(root)

    sub_stats = stats[Path("/data/sub")]
    assert sub_stats.fastq == 400
    assert sub_stats.fasta == 0
    assert sub_stats.sam == 0
    assert sub_stats.vcf == 0


# --- compute_global_metrics ---


def test_global_metrics_match_root_node() -> None:
    root = build_bio_tree()
    uncompressed = compute_all_uncompressed_stats(root)
    metrics = compute_global_metrics(root, uncompressed)

    assert metrics.total_usage_bytes == root.total_bytes
    assert metrics.total_files == root.total_files
    assert metrics.total_uncompressed == uncompressed[root.path]


# --- compute_directory_deltas ---


def test_deltas_detect_growth() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 100)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 300)])

    deltas = compute_directory_deltas(curr_root, prev_root)
    assert Path("/data") in deltas
    assert deltas[Path("/data")].delta_bytes == 200


def test_deltas_detect_shrinkage() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 500)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 200)])

    deltas = compute_directory_deltas(curr_root, prev_root)
    assert deltas[Path("/data")].delta_bytes == -300


def test_deltas_only_include_paths_present_in_both_snapshots() -> None:
    prev_root = make_dir(
        Path("/data"),
        [
            make_dir(Path("/data/old"), [make_file(Path("/data/old"), "x.txt", 10)]),
        ],
    )
    curr_root = make_dir(
        Path("/data"),
        [
            make_dir(Path("/data/new"), [make_file(Path("/data/new"), "y.txt", 20)]),
        ],
    )

    deltas = compute_directory_deltas(curr_root, prev_root)
    # Root is in both, but /data/old and /data/new are not shared
    assert Path("/data") in deltas
    assert Path("/data/old") not in deltas
    assert Path("/data/new") not in deltas


# --- build_drilldown_tree ---


def build_wide_tree(n_children: int) -> NcduDir:
    """Directory with n_children files of decreasing size."""
    root_path = Path("/wide")
    children = [
        make_file(root_path, f"f{i}.dat", (n_children - i) * 100) for i in range(n_children)
    ]
    return make_dir(root_path, children)


def test_drilldown_prunes_to_top_n() -> None:
    root = build_wide_tree(10)
    uncompressed = compute_all_uncompressed_stats(root)
    drilldown = build_drilldown_tree(root, uncompressed, top_n=3, max_depth=5)

    # 3 kept + 1 "Other" node
    assert len(drilldown.children) == 4
    other = drilldown.children[-1]
    assert "Other" in str(other.path)
    assert "7 items" in str(other.path)


def test_drilldown_keeps_all_when_fewer_than_top_n() -> None:
    root = build_wide_tree(3)
    uncompressed = compute_all_uncompressed_stats(root)
    drilldown = build_drilldown_tree(root, uncompressed, top_n=10, max_depth=5)

    assert len(drilldown.children) == 3
    assert all("Other" not in str(c.path) for c in drilldown.children)


def test_drilldown_respects_max_depth() -> None:
    # 3 levels deep: /a -> /a/b -> /a/b/c -> file
    root_path = Path("/a")
    inner = make_dir(
        root_path / "b" / "c",
        [make_file(root_path / "b" / "c", "file.txt", 10)],
    )
    mid = make_dir(root_path / "b", [inner])
    root = make_dir(root_path, [mid])
    uncompressed = compute_all_uncompressed_stats(root)

    drilldown = build_drilldown_tree(root, uncompressed, top_n=10, max_depth=2)

    # depth 0 = /a, depth 1 = /a/b, depth 2 = /a/b/c (hit limit, becomes leaf)
    assert drilldown.children[0].children[0].children == []


def test_drilldown_other_node_bytes_equal_remainder_sum() -> None:
    root = build_wide_tree(6)
    uncompressed = compute_all_uncompressed_stats(root)
    drilldown = build_drilldown_tree(root, uncompressed, top_n=2, max_depth=5)

    other = drilldown.children[-1]
    # Kept: f0 (600), f1 (500). Remainder: f2 (400) + f3 (300) + f4 (200) + f5 (100) = 1000
    assert other.total_bytes == 1000


# --- build_growth_drilldown / build_shrinkage_drilldown ---


def test_growth_drilldown_returns_none_when_nothing_grew() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 500)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 200)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_growth_drilldown(curr_root, deltas, top_n=10, max_depth=5)
    assert result is None


def test_growth_drilldown_captures_positive_deltas() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 100)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 400)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_growth_drilldown(curr_root, deltas, top_n=10, max_depth=5)
    assert result is not None
    assert result.total_bytes == 300


def test_shrinkage_drilldown_captures_negative_deltas() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 500)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 200)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_shrinkage_drilldown(curr_root, deltas, top_n=10, max_depth=5)
    assert result is not None
    assert result.total_bytes == 300


def test_shrinkage_drilldown_returns_none_when_nothing_shrunk() -> None:
    prev_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 100)])
    curr_root = make_dir(Path("/data"), [make_file(Path("/data"), "a.txt", 400)])
    deltas = compute_directory_deltas(curr_root, prev_root)

    result = build_shrinkage_drilldown(curr_root, deltas, top_n=10, max_depth=5)
    assert result is None


# --- extract_project_sample ---


def make_run(
    root_name: str = "/data", dsize: int = 100, timestamp_epoch: int | None = 1700000000
) -> NcduRun:
    root = make_dir(Path(root_name), [make_file(Path(root_name), "a.txt", dsize)])
    ts = None
    if timestamp_epoch is not None:
        ts = datetime.datetime.fromtimestamp(timestamp_epoch, tz=datetime.timezone.utc)
    return NcduRun(root=root, timestamp=ts)


def test_extract_project_sample_basic() -> None:
    run = make_run("/proj", dsize=500, timestamp_epoch=1700000000)
    sample = extract_project_sample(run)

    assert sample.project == "proj"
    assert sample.date == datetime.date(2023, 11, 14)
    assert sample.total_bytes == 500
    assert sample.total_files == 1
    assert sample.total_directories == 1


def test_extract_project_sample_missing_timestamp() -> None:
    run = make_run(timestamp_epoch=None)

    import pytest

    with pytest.raises(ValueError, match="no timestamp"):
        extract_project_sample(run)


def test_extract_project_sample_includes_uncompressed() -> None:
    root_path = Path("/bio")
    root = make_dir(root_path, [make_file(root_path, "reads.fastq", 1000)])
    ts = datetime.datetime.fromtimestamp(1700000000, tz=datetime.timezone.utc)
    run = NcduRun(root=root, timestamp=ts)

    sample = extract_project_sample(run)
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
