from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from durep.ncdu import (
    CollapsedNode,
    NcduDir,
    NcduEntry,
    NcduFile,
    NcduRun,
    UncompressedStats,
    full_path,
    path_str,
)


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


# Directory changes include additions/deletions; file comparisons require retained matches.
@dataclass(slots=True)
class PathDelta:
    path: str
    current_bytes: int
    previous_bytes: int
    direct_delta_bytes: int | None

    @property
    def delta_bytes(self) -> int:
        return self.current_bytes - self.previous_bytes


def compute_global_metrics(root: NcduDir) -> GlobalMetrics:
    return GlobalMetrics(
        total_usage_bytes=root.total_bytes,
        total_files=root.total_files,
        total_uncompressed=root.uncompressed,
    )


def compute_directory_deltas(current: NcduRun, previous: NcduRun) -> dict[str, PathDelta]:
    current_nodes = _collect_all_nodes(current.root)
    previous_nodes = _collect_all_nodes(previous.root)
    deltas: dict[str, PathDelta] = {}
    for path in sorted(current.directories.keys() | previous.directories.keys()):
        cur = current.directories.get(path)
        prev = previous.directories.get(path)
        deltas[path] = PathDelta(
            path=path,
            current_bytes=cur.total_bytes if cur else 0,
            previous_bytes=prev.total_bytes if prev else 0,
            direct_delta_bytes=(cur.direct_bytes if cur else 0)
            - (prev.direct_bytes if prev else 0),
        )

    for path, cur_node in current_nodes.items():
        prev_node = previous_nodes.get(path)
        if isinstance(cur_node, NcduFile) and isinstance(prev_node, NcduFile):
            deltas[path] = PathDelta(
                path=path,
                current_bytes=cur_node.total_bytes,
                previous_bytes=prev_node.total_bytes,
                direct_delta_bytes=None,
            )
    return deltas


def _collect_all_nodes(root: NcduDir) -> dict[str, NcduEntry]:
    result: dict[str, NcduEntry] = {}
    stack: list[NcduEntry] = [root]
    while stack:
        node = stack.pop()
        # Synthetic labels are not stable identities and can collide with real names.
        if not isinstance(node, CollapsedNode):
            result[path_str(node)] = node
        if isinstance(node, NcduDir):
            stack.extend(node.children)
    return result


def node_uncompressed_stats(node: NcduEntry) -> UncompressedStats:
    if isinstance(node, CollapsedNode):
        return UncompressedStats.from_total_size(node.uncompressed_bytes)
    return node.uncompressed


def build_drilldown_tree(
    root: NcduDir,
    top_n: int,
    deltas: dict[str, PathDelta] | None = None,
) -> DrilldownNode:
    return _build_drilldown(root, top_n, deltas)


def _build_drilldown(
    node: NcduEntry,
    top_n: int,
    deltas: dict[str, PathDelta] | None,
) -> DrilldownNode:
    node_path = full_path(node)
    node_key = str(node_path)
    delta = deltas.get(node_key) if deltas else None
    if isinstance(node, CollapsedNode) and not node.is_directory:
        delta = None  # Group membership may differ between scans.
    if isinstance(node, NcduFile) and delta is not None and delta.direct_delta_bytes is not None:
        delta = None  # A directory was replaced by a file at this path.
    prev = delta.previous_bytes if delta else None

    if isinstance(node, (NcduFile, CollapsedNode)):
        return DrilldownNode(
            path=node_path,
            node_type="file",
            total_bytes=node.total_bytes,
            uncompressed=node_uncompressed_stats(node),
            previous_bytes=prev,
        )

    sorted_children = sorted(node.children, key=lambda c: c.total_bytes, reverse=True)
    kept = sorted_children[:top_n]
    remainder = sorted_children[top_n:]

    children = [_build_drilldown(c, top_n, deltas) for c in kept]

    if remainder:
        other_bytes = sum(c.total_bytes for c in remainder)
        other_stats = UncompressedStats.zero()
        for c in remainder:
            other_stats.add_to_self(node_uncompressed_stats(c))
        children.append(
            DrilldownNode(
                path=node_path / f"Other ({len(remainder)} items)",
                node_type="file",
                total_bytes=other_bytes,
                uncompressed=other_stats,
                previous_bytes=None,  # "Other" does not have stable membership across scans.
            )
        )

    return DrilldownNode(
        path=node_path,
        node_type="dir",
        total_bytes=node.total_bytes,
        uncompressed=node.uncompressed,
        children=children,
        previous_bytes=prev,
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
    __slots__ = ("bytes_values", "dates", "measured", "project", "uncompressed_values")

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
        other=round(a.other + t * (b.other - a.other)),
    )
