from __future__ import annotations

import datetime
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from durep.ncdu import NcduDir, NcduEntry, NcduFile, NcduRun

EXTENSIONS = {
    "fasta": ["fna", "faa", "fasta", "fa"],
    "fastq": ["fastq", "fq"],
    "sam": ["sam"],
    "vcf": ["vcf"],
}

# Reverse lookup: extension string -> format name
EXTENSION_TO_FORMAT: dict[str, str] = {}
for fmt, exts in EXTENSIONS.items():
    for ext in exts:
        EXTENSION_TO_FORMAT[ext] = fmt


# Users might want information about data that is taking up more space
# that necessary. These formats are common in bioinformatics, large, and
# typically compressed, so seeing them uncompressed is a sign of waste.
@dataclass(slots=True)
class UncompressedStats:
    fasta: int
    fastq: int
    vcf: int
    sam: int

    @classmethod
    def from_file_node(cls, node: NcduFile) -> UncompressedStats:
        ext = node.path.suffix.lstrip(".")
        fmt = EXTENSION_TO_FORMAT.get(ext)
        if fmt is None:
            return UncompressedStats(0, 0, 0, 0)

        size = node.disk_size
        if fmt == "fasta":
            return UncompressedStats(size, 0, 0, 0)
        elif fmt == "fastq":
            return UncompressedStats(0, size, 0, 0)
        elif fmt == "vcf":
            return UncompressedStats(0, 0, size, 0)
        elif fmt == "sam":
            return UncompressedStats(0, 0, 0, size)
        else:
            assert False  # unreachable

    @property
    def total_size(self) -> int:
        return self.fasta + self.fastq + self.vcf + self.sam

    @classmethod
    def zero(cls) -> UncompressedStats:
        return cls(0, 0, 0, 0)


# Metrics reported for one project or user
@dataclass(slots=True)
class GlobalMetrics:
    total_usage_bytes: int
    total_files: int
    total_uncompressed: UncompressedStats


# Similar to NcduNode, but this stores the information
# after filtering/pruning etc; the data we want to render.
@dataclass(slots=True)
class DrilldownNode:
    path: Path
    node_type: Literal["file", "dir"]
    total_bytes: int
    uncompressed: UncompressedStats
    children: list[DrilldownNode] = field(default_factory=list)
    previous_bytes: int | None = None

    @property
    def delta_bytes(self) -> int | None:
        if self.previous_bytes is None:
            return None
        return self.total_bytes - self.previous_bytes


# If a path occurs in NCDUs at different times, this class is used to store
# whether data under that path grew or shrunk
@dataclass(slots=True)
class PathDelta:
    path: Path
    current_bytes: int
    previous_bytes: int

    @property
    def delta_bytes(self) -> int:
        return self.current_bytes - self.previous_bytes


def compute_all_uncompressed_stats(root: NcduDir) -> dict[Path, UncompressedStats]:
    """Single post-order pass: each node is visited exactly once."""
    result: dict[Path, UncompressedStats] = {}
    # Post-order: push nodes, then process after all children are done.
    # We use (node, visited) pairs; first visit pushes children, second collects.
    stack: list[tuple[NcduEntry, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if isinstance(node, NcduFile):
            result[node.path] = UncompressedStats.from_file_node(node)
        elif visited:
            stats = UncompressedStats.zero()
            for child in node.children:
                child_stats = result[child.path]
                stats.fasta += child_stats.fasta
                stats.fastq += child_stats.fastq
                stats.sam += child_stats.sam
                stats.vcf += child_stats.vcf
            result[node.path] = stats
        else:
            stack.append((node, True))
            for child in node.children:
                stack.append((child, False))
    return result


def compute_global_metrics(
    root: NcduDir, uncompressed: dict[Path, UncompressedStats]
) -> GlobalMetrics:
    return GlobalMetrics(
        total_usage_bytes=root.total_bytes,
        total_files=root.total_files,
        total_uncompressed=uncompressed[root.path],
    )


def compute_directory_deltas(current: NcduDir, previous: NcduDir) -> dict[Path, PathDelta]:
    current_nodes = _collect_all_nodes(current)
    previous_nodes = _collect_all_nodes(previous)
    deltas: dict[Path, PathDelta] = {}
    for path, cur_node in current_nodes.items():
        prev_node = previous_nodes.get(path)
        if prev_node is not None:
            deltas[path] = PathDelta(
                path=path,
                current_bytes=cur_node.total_bytes,
                previous_bytes=prev_node.total_bytes,
            )
    return deltas


def _collect_all_nodes(root: NcduDir) -> dict[Path, NcduEntry]:
    result: dict[Path, NcduEntry] = {}
    stack: list[NcduEntry] = [root]
    while stack:
        node = stack.pop()
        result[node.path] = node
        if isinstance(node, NcduDir):
            stack.extend(node.children)
    return result


def build_drilldown_tree(
    root: NcduDir,
    uncompressed: dict[Path, UncompressedStats],
    top_n: int,
    max_depth: int,
    deltas: dict[Path, PathDelta] | None = None,
) -> DrilldownNode:
    return _build_drilldown(root, uncompressed, top_n, max_depth, deltas, depth=0)


def collapsed_previous_bytes(
    deltas: dict[Path, PathDelta] | None,
    parent_path: Path,
    distinct_nodes: Sequence[NcduEntry],
) -> int | None:
    """Compute previous_bytes for a synthetic collapsed node.

    previous_collapsed = previous_parent_total
                       - sum(previous_size of each distinct node that existed previously)
    """
    if not deltas:
        return None
    parent_delta = deltas.get(parent_path)
    if parent_delta is None:
        return None
    prev_parent = parent_delta.previous_bytes
    prev_distinct = sum(deltas[c.path].previous_bytes for c in distinct_nodes if c.path in deltas)
    return prev_parent - prev_distinct


def _build_drilldown(
    node: NcduEntry,
    uncompressed: dict[Path, UncompressedStats],
    top_n: int,
    max_depth: int,
    deltas: dict[Path, PathDelta] | None,
    depth: int,
) -> DrilldownNode:
    node_stats = uncompressed[node.path]
    delta = deltas.get(node.path) if deltas else None
    prev = delta.previous_bytes if delta else None

    if isinstance(node, NcduFile) or depth >= max_depth:
        return DrilldownNode(
            path=node.path,
            node_type="file" if isinstance(node, NcduFile) else "dir",
            total_bytes=node.total_bytes,
            uncompressed=node_stats,
            previous_bytes=prev,
        )

    # At the last expanded level, coalesce child subdirectories into a single
    # synthetic node so that files from deeper levels don't leak into this ring.
    at_depth_limit = depth == max_depth - 1

    file_children = [c for c in node.children if isinstance(c, NcduFile)]
    dir_children = [c for c in node.children if isinstance(c, NcduDir)]

    if at_depth_limit and dir_children:
        sorted_files = sorted(file_children, key=lambda c: c.total_bytes, reverse=True)
        kept_files = sorted_files[:top_n]
        remainder_files = sorted_files[top_n:]

        children = [
            _build_drilldown(c, uncompressed, top_n, max_depth, deltas, depth + 1)
            for c in kept_files
        ]

        # Aggregate all subdirectories into one synthetic leaf
        subdirs_bytes = sum(c.total_bytes for c in dir_children)
        subdirs_stats = UncompressedStats.zero()
        for c in dir_children:
            s = uncompressed[c.path]
            subdirs_stats.fasta += s.fasta
            subdirs_stats.fastq += s.fastq
            subdirs_stats.sam += s.sam
            subdirs_stats.vcf += s.vcf
        children.append(
            DrilldownNode(
                path=node.path / f"{len(dir_children)} subdirectories",
                node_type="file",
                total_bytes=subdirs_bytes,
                uncompressed=subdirs_stats,
                previous_bytes=collapsed_previous_bytes(deltas, node.path, kept_files),
            )
        )

        if remainder_files:
            other_bytes = sum(c.total_bytes for c in remainder_files)
            other_stats = UncompressedStats.zero()
            for c in remainder_files:
                s = uncompressed[c.path]
                other_stats.fasta += s.fasta
                other_stats.fastq += s.fastq
                other_stats.sam += s.sam
                other_stats.vcf += s.vcf
            children.append(
                DrilldownNode(
                    path=node.path / f"Other ({len(remainder_files)} files)",
                    node_type="file",
                    total_bytes=other_bytes,
                    uncompressed=other_stats,
                    previous_bytes=collapsed_previous_bytes(
                        deltas, node.path, kept_files + dir_children
                    ),
                )
            )
    else:
        sorted_children = sorted(node.children, key=lambda c: c.total_bytes, reverse=True)
        kept = sorted_children[:top_n]
        remainder = sorted_children[top_n:]

        children = [
            _build_drilldown(c, uncompressed, top_n, max_depth, deltas, depth + 1) for c in kept
        ]

        if remainder:
            other_bytes = sum(c.total_bytes for c in remainder)
            other_stats = UncompressedStats.zero()
            for c in remainder:
                s = uncompressed[c.path]
                other_stats.fasta += s.fasta
                other_stats.fastq += s.fastq
                other_stats.sam += s.sam
                other_stats.vcf += s.vcf
            children.append(
                DrilldownNode(
                    path=node.path / f"Other ({len(remainder)} items)",
                    node_type="file",
                    total_bytes=other_bytes,
                    uncompressed=other_stats,
                    previous_bytes=collapsed_previous_bytes(deltas, node.path, kept),
                )
            )

    return DrilldownNode(
        path=node.path,
        node_type="dir",
        total_bytes=node.total_bytes,
        uncompressed=node_stats,
        children=children,
        previous_bytes=prev,
    )


def build_growth_drilldown(
    current: NcduDir,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
) -> DrilldownNode | None:
    return _build_delta_drilldown(current, deltas, top_n, max_depth, sign=1)


def build_shrinkage_drilldown(
    current: NcduDir,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
) -> DrilldownNode | None:
    return _build_delta_drilldown(current, deltas, top_n, max_depth, sign=-1)


def _build_delta_drilldown(
    current: NcduDir,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
    sign: int,
) -> DrilldownNode | None:
    result = _build_delta_node(current, deltas, top_n, max_depth, sign, depth=0)
    if result is None or result.total_bytes <= 0:
        return None
    return result


def _build_delta_node(
    node: NcduEntry,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
    sign: int,
    depth: int,
) -> DrilldownNode | None:
    delta = deltas.get(node.path)
    if delta is None:
        return None
    # sign=1 keeps positive deltas (growth), sign=-1 keeps negative (shrinkage, flipped to positive)
    magnitude = delta.delta_bytes * sign
    if magnitude <= 0:
        return None

    if isinstance(node, NcduFile) or depth >= max_depth:
        return DrilldownNode(
            path=node.path,
            node_type="file" if isinstance(node, NcduFile) else "dir",
            total_bytes=magnitude,
            uncompressed=UncompressedStats.zero(),
        )

    child_results: list[DrilldownNode] = []
    for child in node.children:
        built = _build_delta_node(child, deltas, top_n, max_depth, sign, depth + 1)
        if built is not None:
            child_results.append(built)

    child_results.sort(key=lambda c: c.total_bytes, reverse=True)
    kept = child_results[:top_n]
    remainder = child_results[top_n:]

    children = list(kept)
    if remainder:
        other_bytes = sum(c.total_bytes for c in remainder)
        children.append(
            DrilldownNode(
                path=node.path / f"Other ({len(remainder)} items)",
                node_type="file",
                total_bytes=other_bytes,
                uncompressed=UncompressedStats.zero(),
            )
        )

    return DrilldownNode(
        path=node.path,
        node_type="dir",
        total_bytes=magnitude,
        uncompressed=UncompressedStats.zero(),
        children=children,
    )


# --- Overview (multi-project time series) ---


@dataclass(slots=True)
class ProjectSample:
    project: str
    timestamp: datetime.datetime
    date: datetime.date
    total_bytes: int
    total_files: int
    total_directories: int
    uncompressed: UncompressedStats


class ProjectTimeSeries:
    __slots__ = ("project", "dates", "bytes_values", "uncompressed_values", "measured")

    def __init__(
        self,
        project: str,
        dates: list[datetime.date],
        bytes_values: list[int],
        uncompressed_values: list[UncompressedStats],
        measured: list[bool],
    ) -> None:
        if not (len(dates) == len(bytes_values) == len(uncompressed_values) == len(measured)):
            raise ValueError(
                f"ProjectTimeSeries list lengths must match: "
                f"dates={len(dates)}, bytes_values={len(bytes_values)}, "
                f"uncompressed_values={len(uncompressed_values)}, "
                f"measured={len(measured)}"
            )
        self.project = project
        self.dates = dates
        self.bytes_values = bytes_values
        self.uncompressed_values = uncompressed_values
        self.measured = measured


def extract_project_sample(run: NcduRun) -> ProjectSample:
    if run.timestamp is None:
        raise ValueError(f"scan for {run.root.path} has no timestamp; overview requires timestamps")
    uncompressed = compute_all_uncompressed_stats(run.root)
    return ProjectSample(
        project=run.root.path.name,
        timestamp=run.timestamp,
        date=run.timestamp.date(),
        total_bytes=run.root.total_bytes,
        total_files=run.root.total_files,
        total_directories=run.root.total_directories,
        uncompressed=uncompressed[run.root.path],
    )


def build_overview_series(
    samples: list[ProjectSample],
) -> list[ProjectTimeSeries]:
    if not samples:
        return []

    # Sort by timestamp so that within each project+day, the latest scan wins
    sorted_samples = sorted(samples, key=lambda s: s.timestamp)

    # Group by project; within each project, keep latest sample per day
    by_project: dict[str, dict[datetime.date, ProjectSample]] = {}
    for sample in sorted_samples:
        day_map = by_project.setdefault(sample.project, {})
        day_map[sample.date] = sample

    # Build complete daily date range
    all_dates: set[datetime.date] = set()
    for day_map in by_project.values():
        all_dates.update(day_map.keys())

    min_date = min(all_dates)
    max_date = max(all_dates)
    date_range: list[datetime.date] = []
    current = min_date
    one_day = datetime.timedelta(days=1)
    while current <= max_date:
        date_range.append(current)
        current += one_day

    # Build time series per project with interpolation:
    #   - Before first measurement: back-fill with first measured value
    #   - Between measurements: linear interpolation (bytes), nearest for uncompressed
    #   - After last measurement: forward-fill with last measured value
    result: list[ProjectTimeSeries] = []
    n_days = len(date_range)
    for project in sorted(by_project):
        day_map = by_project[project]

        # Collect measured indices and their values
        measured_indices: list[int] = []
        measured_bytes: list[int] = []
        measured_uncompressed: list[UncompressedStats] = []
        for i, date in enumerate(date_range):
            sample = day_map.get(date)
            if sample is not None:
                measured_indices.append(i)
                measured_bytes.append(sample.total_bytes)
                measured_uncompressed.append(sample.uncompressed)

        measured: list[bool] = [False] * n_days
        bytes_values: list[int] = [0] * n_days
        uncompressed_values: list[UncompressedStats] = [UncompressedStats.zero()] * n_days

        if measured_indices:
            # Mark measured points
            for k, i in enumerate(measured_indices):
                measured[i] = True
                bytes_values[i] = measured_bytes[k]
                uncompressed_values[i] = measured_uncompressed[k]

            # Back-fill before first measurement
            first = measured_indices[0]
            for i in range(first):
                bytes_values[i] = measured_bytes[0]
                uncompressed_values[i] = measured_uncompressed[0]

            # Forward-fill after last measurement
            last = measured_indices[-1]
            for i in range(last + 1, n_days):
                bytes_values[i] = measured_bytes[-1]
                uncompressed_values[i] = measured_uncompressed[-1]

            # Linearly interpolate between measured points
            for k in range(len(measured_indices) - 1):
                i0 = measured_indices[k]
                i1 = measured_indices[k + 1]
                b0 = measured_bytes[k]
                b1 = measured_bytes[k + 1]
                span = i1 - i0
                for i in range(i0 + 1, i1):
                    t = (i - i0) / span
                    bytes_values[i] = round(b0 + t * (b1 - b0))
                    uncompressed_values[i] = interpolate_uncompressed(
                        measured_uncompressed[k], measured_uncompressed[k + 1], t
                    )

        result.append(
            ProjectTimeSeries(
                project=project,
                dates=list(date_range),
                bytes_values=bytes_values,
                uncompressed_values=uncompressed_values,
                measured=measured,
            )
        )

    return result


def interpolate_uncompressed(
    a: UncompressedStats, b: UncompressedStats, t: float
) -> UncompressedStats:
    return UncompressedStats(
        fasta=round(a.fasta + t * (b.fasta - a.fasta)),
        fastq=round(a.fastq + t * (b.fastq - a.fastq)),
        vcf=round(a.vcf + t * (b.vcf - a.vcf)),
        sam=round(a.sam + t * (b.sam - a.sam)),
    )
