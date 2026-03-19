from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from durep.ncdu import NcduNode

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
    def from_file_node(cls, node: NcduNode) -> UncompressedStats:
        assert node.node_type == "file"
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


def compute_all_uncompressed_stats(root: NcduNode) -> dict[Path, UncompressedStats]:
    """Single post-order pass: each node is visited exactly once."""
    result: dict[Path, UncompressedStats] = {}
    # Post-order: push nodes, then process after all children are done.
    # We use (node, visited) pairs; first visit pushes children, second collects.
    stack: list[tuple[NcduNode, bool]] = [(root, False)]
    while stack:
        node, visited = stack.pop()
        if node.node_type == "file":
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
    root: NcduNode, uncompressed: dict[Path, UncompressedStats]
) -> GlobalMetrics:
    return GlobalMetrics(
        total_usage_bytes=root.total_bytes,
        total_files=root.total_files,
        total_uncompressed=uncompressed[root.path],
    )


def compute_directory_deltas(current: NcduNode, previous: NcduNode) -> dict[Path, PathDelta]:
    current_dirs = _collect_directories(current)
    previous_dirs = _collect_directories(previous)
    deltas: dict[Path, PathDelta] = {}
    for path, cur_node in current_dirs.items():
        prev_node = previous_dirs.get(path)
        if prev_node is not None:
            deltas[path] = PathDelta(
                path=path,
                current_bytes=cur_node.total_bytes,
                previous_bytes=prev_node.total_bytes,
            )
    return deltas


def _collect_directories(root: NcduNode) -> dict[Path, NcduNode]:
    result: dict[Path, NcduNode] = {}
    stack = [root]
    while stack:
        node = stack.pop()
        if node.node_type == "dir":
            result[node.path] = node
            stack.extend(node.children)
    return result


def build_drilldown_tree(
    root: NcduNode,
    uncompressed: dict[Path, UncompressedStats],
    top_n: int,
    max_depth: int,
) -> DrilldownNode:
    return _build_drilldown(root, uncompressed, top_n, max_depth, depth=0)


def _build_drilldown(
    node: NcduNode,
    uncompressed: dict[Path, UncompressedStats],
    top_n: int,
    max_depth: int,
    depth: int,
) -> DrilldownNode:
    node_stats = uncompressed[node.path]

    if node.node_type == "file" or depth >= max_depth:
        return DrilldownNode(
            path=node.path,
            node_type=node.node_type,
            total_bytes=node.total_bytes,
            uncompressed=node_stats,
        )

    # At the last expanded level, coalesce child subdirectories into a single
    # synthetic node so that files from deeper levels don't leak into this ring.
    at_depth_limit = depth == max_depth - 1

    file_children = [c for c in node.children if c.node_type == "file"]
    dir_children = [c for c in node.children if c.node_type == "dir"]

    if at_depth_limit and dir_children:
        sorted_files = sorted(file_children, key=lambda c: c.total_bytes, reverse=True)
        kept_files = sorted_files[:top_n]
        remainder_files = sorted_files[top_n:]

        children = [
            _build_drilldown(c, uncompressed, top_n, max_depth, depth + 1) for c in kept_files
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
                )
            )
    else:
        sorted_children = sorted(node.children, key=lambda c: c.total_bytes, reverse=True)
        kept = sorted_children[:top_n]
        remainder = sorted_children[top_n:]

        children = [_build_drilldown(c, uncompressed, top_n, max_depth, depth + 1) for c in kept]

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
                )
            )

    return DrilldownNode(
        path=node.path,
        node_type="dir",
        total_bytes=node.total_bytes,
        uncompressed=node_stats,
        children=children,
    )


def build_growth_drilldown(
    current: NcduNode,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
) -> DrilldownNode | None:
    return _build_delta_drilldown(current, deltas, top_n, max_depth, sign=1)


def build_shrinkage_drilldown(
    current: NcduNode,
    deltas: dict[Path, PathDelta],
    top_n: int,
    max_depth: int,
) -> DrilldownNode | None:
    return _build_delta_drilldown(current, deltas, top_n, max_depth, sign=-1)


def _build_delta_drilldown(
    current: NcduNode,
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
    node: NcduNode,
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

    if node.node_type == "file" or depth >= max_depth:
        return DrilldownNode(
            path=node.path,
            node_type=node.node_type,
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
