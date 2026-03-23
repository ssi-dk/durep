from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import ijson  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from durep.analytics import ProjectSample

EXTENSIONS = {
    "fasta": ["fna", "faa", "fasta", "fa"],
    "fastq": ["fastq", "fq"],
    "sam": ["sam"],
    "vcf": ["vcf"],
}

# Reverse lookup: extension string -> format name
EXTENSION_TO_FORMAT: dict[str, str] = {}
for _fmt, _exts in EXTENSIONS.items():
    for _ext in _exts:
        EXTENSION_TO_FORMAT[_ext] = _fmt


@dataclass(slots=True)
class UncompressedStats:
    fasta: int
    fastq: int
    vcf: int
    sam: int

    @property
    def total_size(self) -> int:
        return self.fasta + self.fastq + self.vcf + self.sam

    @classmethod
    def zero(cls) -> UncompressedStats:
        return cls(0, 0, 0, 0)

    @classmethod
    def from_file_node(cls, basename: str, disk_size: int) -> UncompressedStats:
        dot = basename.rfind(".")
        ext = basename[dot + 1 :] if dot >= 0 else ""
        fmt = EXTENSION_TO_FORMAT.get(ext)
        if fmt is None:
            return UncompressedStats(0, 0, 0, 0)
        if fmt == "fasta":
            return UncompressedStats(disk_size, 0, 0, 0)
        elif fmt == "fastq":
            return UncompressedStats(0, disk_size, 0, 0)
        elif fmt == "vcf":
            return UncompressedStats(0, 0, disk_size, 0)
        elif fmt == "sam":
            return UncompressedStats(0, 0, 0, disk_size)
        else:
            assert False  # unreachable


@dataclass(slots=True)
class NcduFile:
    basename: str
    parent: NcduDir
    disk_size: int
    uncompressed: UncompressedStats

    @property
    def total_bytes(self) -> int:
        return self.disk_size


@dataclass(slots=True)
class NcduDir:
    basename: str
    parent: NcduDir | None  # None for root
    disk_size: int
    total_bytes: int
    total_files: int
    total_directories: int
    uncompressed: UncompressedStats
    children: list[NcduEntry] = field(default_factory=list)


@dataclass(slots=True)
class CollapsedNode:
    basename: str
    parent: NcduDir
    count: int
    disk_size: int
    total_bytes: int
    uncompressed: UncompressedStats


NcduEntry = NcduDir | NcduFile | CollapsedNode


def path_str(node: NcduEntry) -> str:
    parts: list[str] = []
    current: NcduEntry = node
    while True:
        parts.append(current.basename)
        if isinstance(current, (NcduFile, CollapsedNode)):
            current = current.parent
        elif current.parent is not None:
            current = current.parent
        else:
            break
    parts.reverse()
    if len(parts) == 1:
        return parts[0]
    root = parts[0]
    rest = "/".join(parts[1:])
    if root.endswith("/"):
        return root + rest
    return root + "/" + rest


def full_path(node: NcduEntry) -> Path:
    return Path(path_str(node))


@dataclass(slots=True)
class NcduRun:
    root: NcduDir
    timestamp: datetime


Event = tuple[str, Any]


@dataclass(slots=True)
class DirAggregate:
    basename: str
    disk_size: int
    total_bytes: int
    total_files: int
    total_directories: int
    uncompressed: UncompressedStats


def parse_ncdu_json_file(path: Path, top_n: int | None = None) -> NcduRun:
    return parse_ncdu_file(path, parse_tree_to_run, top_n)


def parse_ncdu_project_sample(path: Path) -> "ProjectSample":
    return parse_ncdu_file(path, parse_tree_to_project_sample)


def parse_ncdu_file(path: Path, parse_body: Callable[..., Any], *parse_args: Any) -> Any:
    error_prefix = f"Invalid NCDU JSON file at {str(path)}: "

    with path.open("rb") as handle:
        try:
            parser: Iterator[Event] = ijson.basic_parse(handle)
            timestamp = parse_header(parser, error_prefix)
            return parse_body(parser, timestamp, *parse_args)
        except (ijson.JSONError, StopIteration) as exc:
            raise ValueError(
                error_prefix + "NCDU JSON file is not a valid version 1 NCDU format file"
            ) from exc


def parse_tree_to_run(parser: Iterator[Event], timestamp: datetime, top_n: int | None) -> NcduRun:
    root = parse_tree_streaming(parser, top_n=top_n)
    if root.parent is None and not Path(root.basename).is_absolute():
        raise ValueError(f"Root node path must be absolute, got: {root.basename}")
    return NcduRun(root=root, timestamp=timestamp)


def parse_tree_to_project_sample(parser: Iterator[Event], timestamp: datetime) -> "ProjectSample":
    from durep.analytics import ProjectSample

    root = parse_tree_aggregate_streaming(parser)
    root_path = Path(root.basename)
    if not root_path.is_absolute():
        raise ValueError(f"Root node path must be absolute, got: {root.basename}")

    root_str = str(root_path)
    project = root_str.rsplit("/", 1)[-1] or root_str
    return ProjectSample(
        project=project,
        timestamp=timestamp,
        date=timestamp.date(),
        total_bytes=root.total_bytes,
        total_files=root.total_files,
        total_directories=root.total_directories,
        uncompressed=root.uncompressed,
    )


def parse_header(parser: Iterator[Event], error_prefix: str) -> datetime:
    # Expect: start_array, then number (major version), number (minor version), then metadata map
    event, value = next(parser)
    if event != "start_array":
        raise ValueError(error_prefix + "NCDU JSON file is not a valid version 1 NCDU format file")

    # Major version
    event, value = next(parser)
    if event != "number" or value != 1:
        raise ValueError(error_prefix + "NCDU JSON file is not a valid version 1 NCDU format file")

    # Minor version — just consume it
    next(parser)

    # Metadata map — accumulate into a dict
    event, value = next(parser)
    if event != "start_map":
        raise ValueError(error_prefix + "Does not contain expected timestamp field in metadata")

    metadata: dict[str, Any] = {}
    key = ""
    for event, value in parser:
        if event == "map_key":
            key = value
        elif event == "end_map":
            break
        else:
            metadata[key] = value

    if "timestamp" not in metadata:
        raise ValueError(error_prefix + "Does not contain expected timestamp field in metadata")

    try:
        timestamp = datetime.fromtimestamp(int(metadata["timestamp"]), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(error_prefix + "could not parse timestamp as POSIX timestamp")

    return timestamp


def collapse_children(directory: NcduDir, top_n: int) -> None:
    if len(directory.children) <= top_n:
        return
    directory.children.sort(key=lambda c: c.total_bytes, reverse=True)
    kept = directory.children[:top_n]
    remainder = directory.children[top_n:]

    total_disk = 0
    total_bytes = 0
    count = 0
    agg = UncompressedStats.zero()
    for c in remainder:
        total_disk += c.disk_size
        total_bytes += c.total_bytes
        agg.fasta += c.uncompressed.fasta
        agg.fastq += c.uncompressed.fastq
        agg.vcf += c.uncompressed.vcf
        agg.sam += c.uncompressed.sam
        if isinstance(c, CollapsedNode):
            count += c.count
        elif isinstance(c, NcduDir):
            count += c.total_files + c.total_directories
        else:
            count += 1

    collapsed = CollapsedNode(
        basename=f"({count} collapsed entries)",
        parent=directory,
        count=count,
        disk_size=total_disk,
        total_bytes=total_bytes,
        uncompressed=agg,
    )
    directory.children = kept + [collapsed]


def parse_tree_streaming(parser: Iterator[Event], top_n: int | None = None) -> NcduDir:
    dir_stack: list[NcduDir] = []
    awaiting_dir_metadata = False
    current_map: dict[str, Any] = {}
    in_map = False
    map_key = ""
    root: NcduDir | None = None

    for event, value in parser:
        if event == "start_array":
            awaiting_dir_metadata = True

        elif event == "start_map":
            in_map = True
            current_map = {}

        elif event == "map_key":
            map_key = value

        elif event == "end_map":
            in_map = False
            if awaiting_dir_metadata:
                awaiting_dir_metadata = False
                parent = dir_stack[-1] if dir_stack else None
                basename = get_required_name(current_map)
                disk_size = parse_disk_size(current_map)
                node = NcduDir(
                    basename=basename,
                    parent=parent,
                    disk_size=disk_size,
                    total_bytes=disk_size,
                    total_files=0,
                    total_directories=1,
                    uncompressed=UncompressedStats.zero(),
                )
                dir_stack.append(node)
            else:
                parent_dir = dir_stack[-1]
                basename = get_required_name(current_map)
                disk_size = parse_disk_size(current_map)
                uncompressed = UncompressedStats.from_file_node(basename, disk_size)
                parent_dir.children.append(
                    NcduFile(
                        basename=basename,
                        parent=parent_dir,
                        disk_size=disk_size,
                        uncompressed=uncompressed,
                    )
                )
                # Incrementally update parent aggregates
                parent_dir.total_bytes += disk_size
                parent_dir.total_files += 1
                parent_dir.uncompressed.fasta += uncompressed.fasta
                parent_dir.uncompressed.fastq += uncompressed.fastq
                parent_dir.uncompressed.vcf += uncompressed.vcf
                parent_dir.uncompressed.sam += uncompressed.sam

        elif event == "end_array":
            if dir_stack:
                finished = dir_stack.pop()
                if top_n is not None:
                    collapse_children(finished, top_n)
                if dir_stack:
                    parent = dir_stack[-1]
                    parent.children.append(finished)
                    parent.total_bytes += finished.total_bytes
                    parent.total_files += finished.total_files
                    parent.total_directories += finished.total_directories
                    parent.uncompressed.fasta += finished.uncompressed.fasta
                    parent.uncompressed.fastq += finished.uncompressed.fastq
                    parent.uncompressed.vcf += finished.uncompressed.vcf
                    parent.uncompressed.sam += finished.uncompressed.sam
                else:
                    root = finished

        elif in_map:
            current_map[map_key] = value

    if root is None:
        raise ValueError("Fourth field (root directory) is not a valid directory in NCDU format")
    return root


def parse_tree_aggregate_streaming(parser: Iterator[Event]) -> DirAggregate:
    dir_stack: list[DirAggregate] = []
    awaiting_dir_metadata = False
    current_map: dict[str, Any] = {}
    in_map = False
    map_key = ""
    root: DirAggregate | None = None

    for event, value in parser:
        if event == "start_array":
            awaiting_dir_metadata = True

        elif event == "start_map":
            in_map = True
            current_map = {}

        elif event == "map_key":
            map_key = value

        elif event == "end_map":
            in_map = False
            if awaiting_dir_metadata:
                awaiting_dir_metadata = False
                disk_size = parse_disk_size(current_map)
                dir_stack.append(
                    DirAggregate(
                        basename=get_required_name(current_map),
                        disk_size=disk_size,
                        total_bytes=disk_size,
                        total_files=0,
                        total_directories=1,
                        uncompressed=UncompressedStats.zero(),
                    )
                )
            else:
                parent = dir_stack[-1]
                disk_size = parse_disk_size(current_map)
                uncompressed = UncompressedStats.from_file_node(
                    get_required_name(current_map), disk_size
                )
                parent.total_bytes += disk_size
                parent.total_files += 1
                parent.uncompressed.fasta += uncompressed.fasta
                parent.uncompressed.fastq += uncompressed.fastq
                parent.uncompressed.vcf += uncompressed.vcf
                parent.uncompressed.sam += uncompressed.sam

        elif event == "end_array":
            if dir_stack:
                finished = dir_stack.pop()
                if dir_stack:
                    parent = dir_stack[-1]
                    parent.total_bytes += finished.total_bytes
                    parent.total_files += finished.total_files
                    parent.total_directories += finished.total_directories
                    parent.uncompressed.fasta += finished.uncompressed.fasta
                    parent.uncompressed.fastq += finished.uncompressed.fastq
                    parent.uncompressed.vcf += finished.uncompressed.vcf
                    parent.uncompressed.sam += finished.uncompressed.sam
                else:
                    root = finished

        elif in_map:
            current_map[map_key] = value

    if root is None:
        raise ValueError("Fourth field (root directory) is not a valid directory in NCDU format")
    return root


def parse_disk_size(entry: dict[str, Any]) -> int:
    return parse_non_negative_int(entry.get("dsize", 0), field_name="dsize")


def get_required_name(entry: dict[str, Any]) -> str:
    raw_name = entry.get("name")
    if not isinstance(raw_name, str) or raw_name.strip() == "":
        raise ValueError("Each ncdu entry must include a non-empty string 'name'")
    return raw_name


def parse_non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for {field_name}: {value}") from exc
    if parsed < 0:
        raise ValueError(f"Negative values are not allowed for {field_name}: {parsed}")
    return parsed
