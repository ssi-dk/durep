from __future__ import annotations

import heapq
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
    "other": ["tsv", "csv", "bed", "gff", "paf", "gfa"],
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
    other: int

    @property
    def total_size(self) -> int:
        return self.fasta + self.fastq + self.vcf + self.sam + self.other

    def add_to_self(self, other: UncompressedStats):
        self.fasta += other.fasta
        self.fastq += other.fastq
        self.vcf += other.vcf
        self.sam += other.sam
        self.other += other.other

    @classmethod
    def zero(cls) -> UncompressedStats:
        return cls(0, 0, 0, 0, 0)

    @classmethod
    def from_file_node(cls, basename: str, disk_size: int) -> UncompressedStats:
        dot = basename.rfind(".")
        ext = basename[dot + 1 :] if dot >= 0 else ""
        fmt = EXTENSION_TO_FORMAT.get(ext)
        if fmt is None:
            return UncompressedStats(0, 0, 0, 0, 0)
        if fmt == "fasta":
            return UncompressedStats(disk_size, 0, 0, 0, 0)
        elif fmt == "fastq":
            return UncompressedStats(0, disk_size, 0, 0, 0)
        elif fmt == "vcf":
            return UncompressedStats(0, 0, disk_size, 0, 0)
        elif fmt == "sam":
            return UncompressedStats(0, 0, 0, disk_size, 0)
        elif fmt == "other":
            return UncompressedStats(0, 0, 0, 0, disk_size)
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


@dataclass(slots=True)
class OpenDirState:
    node: NcduDir
    next_child_order: int = 0
    dir_children: list[tuple[int, NcduDir]] = field(default_factory=list)
    kept_files: list[tuple[int, int, NcduFile]] = field(default_factory=list)
    collapsed_count: int = 0
    collapsed_disk_size: int = 0
    collapsed_total_bytes: int = 0
    collapsed_uncompressed: UncompressedStats = field(default_factory=UncompressedStats.zero)


def path_str(node: NcduEntry) -> str:
    "Get absolute path of node by traverse up the tree"
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


def parse_ncdu_json_file(path: Path, top_n: int = 20, display_nodes: int = 5000) -> NcduRun:
    return parse_ncdu_file(path, parse_tree_to_run, top_n, display_nodes)


def parse_ncdu_project_sample(path: Path) -> "ProjectSample":
    return parse_ncdu_file(path, parse_tree_to_project_sample)


def parse_ncdu_file(path: Path, parse_body: Callable[..., Any], *parse_args: Any) -> Any:
    error_prefix = f"Invalid NCDU JSON file at {str(path)}: "

    with path.open("rb") as handle:
        try:
            parser: Iterator[Event] = ijson.basic_parse(handle)
            timestamp = parse_header(parser, error_prefix)
            return parse_body(parser, timestamp, *parse_args)
        except ijson.JSONError as exc:
            raise ValueError(error_prefix + str(exc)) from exc
        except StopIteration as exc:
            raise ValueError(
                error_prefix + "NCDU JSON file is not a valid version 1 NCDU format file"
            ) from exc
        except ValueError as exc:
            if str(exc).startswith(error_prefix):
                raise
            raise ValueError(error_prefix + str(exc)) from exc


def parse_tree_to_run(
    parser: Iterator[Event], timestamp: datetime, top_n: int, display_nodes: int
) -> NcduRun:
    "Only store top_n largest direct files in each directory, collapse the rest"

    root = parse_tree_streaming(parser, top_n=top_n)
    if root.parent is None and not Path(root.basename).is_absolute():
        raise ValueError(f"Root node path must be absolute, got: {root.basename}")
    apply_node_budget(root, display_nodes)
    return NcduRun(root=root, timestamp=timestamp)


def parse_tree_to_project_sample(parser: Iterator[Event], timestamp: datetime) -> "ProjectSample":
    from durep.analytics import ProjectSample

    awaiting_dir_metadata = False
    current_map: dict[str, Any] = {}
    in_map = False
    map_key = ""
    directory_depth = 0

    root_basename: str | None = None
    total_bytes = 0
    total_files = 0
    total_directories = 0
    uncompressed = UncompressedStats.zero()

    for event, value in parser:
        if event == "start_array":
            awaiting_dir_metadata = True
            directory_depth += 1

        elif event == "start_map":
            in_map = True
            current_map.clear()

        elif event == "map_key":
            map_key = value

        elif event == "end_map":
            in_map = False
            if awaiting_dir_metadata:
                awaiting_dir_metadata = False
                basename = get_required_name(current_map)
                disk_size = parse_disk_size(current_map)
                if root_basename is None:
                    root_basename = basename
                total_bytes += disk_size
                total_directories += 1
            else:
                if directory_depth <= 0:
                    raise ValueError(
                        "Fourth field (root directory) is not a valid directory in NCDU format"
                    )
                basename = get_required_name(current_map)
                disk_size = parse_disk_size(current_map)
                file_uncompressed = UncompressedStats.from_file_node(basename, disk_size)
                total_bytes += disk_size
                total_files += 1
                uncompressed.add_to_self(file_uncompressed)

        elif event == "end_array":
            if directory_depth > 0:
                directory_depth -= 1

        elif in_map:
            current_map[map_key] = value

    if root_basename is None or directory_depth != 0:
        raise ValueError("Fourth field (root directory) is not a valid directory in NCDU format")

    root_path = Path(root_basename)
    if not root_path.is_absolute():
        raise ValueError(f"Root node path must be absolute, got: {root_basename}")

    root_str = str(root_path)
    project = root_str.rsplit("/", 1)[-1] or root_str
    return ProjectSample(
        project=project,
        timestamp=timestamp,
        date=timestamp.date(),
        total_bytes=total_bytes,
        total_files=total_files,
        total_directories=total_directories,
        uncompressed=uncompressed,
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
    files: list[NcduFile] = []
    for child in directory.children:
        if isinstance(child, NcduDir):
            continue
        elif isinstance(child, CollapsedNode):
            assert False  # should never appear here
        elif isinstance(child, NcduFile):
            files.append(child)

    if len(files) <= top_n:
        return

    files.sort(key=lambda child: child.total_bytes, reverse=True)
    to_collapse = files[top_n:]
    to_collapse_ids = {id(child) for child in to_collapse}

    total_disk = 0
    total_bytes = 0
    agg = UncompressedStats.zero()
    for c in to_collapse:
        total_disk += c.disk_size
        total_bytes += c.total_bytes
        agg.add_to_self(c.uncompressed)

    collapsed = CollapsedNode(
        basename=f"({len(to_collapse)} collapsed entries)",
        parent=directory,
        count=len(to_collapse),
        disk_size=total_disk,
        total_bytes=total_bytes,
        uncompressed=agg,
    )

    directory.children = [child for child in directory.children if id(child) not in to_collapse_ids]
    directory.children.append(collapsed)


def apply_node_budget(root: NcduDir, budget: int) -> None:
    """Collapse directories to keep total display nodes within *budget*.

    Uses greedy size-priority expansion: starting from the root, expand
    the largest directories first until the budget is exhausted.  Directories
    that are not expanded are replaced with :class:`CollapsedNode` leaves.
    """
    # Phase 1 — decide which directories to expand using a max-heap.
    expanded: set[int] = {id(root)}
    node_count = 1 + len(root.children)

    heap: list[tuple[int, int, NcduDir]] = []
    counter = 0
    for child in root.children:
        if isinstance(child, NcduDir):
            heapq.heappush(heap, (-child.total_bytes, counter, child))
            counter += 1

    while heap:
        _neg_bytes, _tie, d = heapq.heappop(heap)
        cost = len(d.children)
        if node_count + cost <= budget:
            node_count += cost
            expanded.add(id(d))
            for child in d.children:
                if isinstance(child, NcduDir):
                    heapq.heappush(heap, (-child.total_bytes, counter, child))
                    counter += 1

    # Phase 2 — collapse every directory that was *not* expanded.
    stack = [root]
    while stack:
        node = stack.pop()
        new_children: list[NcduEntry] = []
        for child in node.children:
            if isinstance(child, NcduDir):
                if id(child) in expanded:
                    stack.append(child)
                    new_children.append(child)
                else:
                    new_children.append(
                        CollapsedNode(
                            basename=child.basename,
                            parent=node,
                            count=child.total_files + child.total_directories,
                            disk_size=child.total_bytes,
                            total_bytes=child.total_bytes,
                            uncompressed=child.uncompressed,
                        )
                    )
            else:
                new_children.append(child)
        node.children = new_children


def add_file_to_open_dir(
    directory: OpenDirState, basename: str, disk_size: int, top_n: int
) -> None:
    file_uncompressed = UncompressedStats.from_file_node(basename, disk_size)
    order = directory.next_child_order
    directory.next_child_order += 1

    directory.node.total_bytes += disk_size
    directory.node.total_files += 1
    directory.node.uncompressed.add_to_self(file_uncompressed)

    if len(directory.kept_files) < top_n:
        heapq.heappush(
            directory.kept_files,
            (
                disk_size,
                order,
                NcduFile(
                    basename=basename,
                    parent=directory.node,
                    disk_size=disk_size,
                    uncompressed=file_uncompressed,
                ),
            ),
        )
        return

    smallest_bytes, _smallest_order, _smallest_file = directory.kept_files[0]
    if disk_size > smallest_bytes:
        _bytes, _order, evicted = heapq.heapreplace(
            directory.kept_files,
            (
                disk_size,
                order,
                NcduFile(
                    basename=basename,
                    parent=directory.node,
                    disk_size=disk_size,
                    uncompressed=file_uncompressed,
                ),
            ),
        )
        aggregate_collapsed_stats(directory, evicted.disk_size, evicted.uncompressed)
    else:
        aggregate_collapsed_stats(directory, disk_size, file_uncompressed)


def aggregate_collapsed_stats(
    directory: OpenDirState, disk_size: int, uncompressed: UncompressedStats
) -> None:
    directory.collapsed_count += 1
    directory.collapsed_disk_size += disk_size
    directory.collapsed_total_bytes += disk_size
    directory.collapsed_uncompressed.add_to_self(uncompressed)


def finalize_open_dir(directory: OpenDirState) -> NcduDir:
    children: list[tuple[int, NcduEntry]] = []
    children.extend(directory.dir_children)
    children.extend((order, file_node) for _bytes, order, file_node in directory.kept_files)
    children.sort(key=lambda child: child[0])
    directory.node.children = [child for _order, child in children]

    if directory.collapsed_count > 0:
        directory.node.children.append(
            CollapsedNode(
                basename=f"({directory.collapsed_count} collapsed entries)",
                parent=directory.node,
                count=directory.collapsed_count,
                disk_size=directory.collapsed_disk_size,
                total_bytes=directory.collapsed_total_bytes,
                uncompressed=directory.collapsed_uncompressed,
            )
        )

    return directory.node


def parse_tree_streaming(parser: Iterator[Event], top_n: int = 20) -> NcduDir:
    dir_stack: list[OpenDirState] = []
    awaiting_dir_metadata = False
    current_map: dict[str, Any] = {}
    in_map = False
    map_key = ""
    root: NcduDir | None = None

    for event, value in parser:
        # Directories are [dir_element, elements...], so the boolean here means the next map
        # contain directory metadata
        if event == "start_array":
            awaiting_dir_metadata = True

        elif event == "start_map":
            in_map = True
            current_map.clear()

        elif event == "map_key":
            map_key = value

        elif event == "end_map":
            # End of directory metadata or file
            in_map = False
            if awaiting_dir_metadata:
                # If end of directory metadata, make the directory and push to stack
                awaiting_dir_metadata = False
                parent = dir_stack[-1].node if dir_stack else None
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
                dir_stack.append(OpenDirState(node=node))
            else:
                # Else, it must be a file. Make a file and append to parent dir
                basename = get_required_name(current_map)
                disk_size = parse_disk_size(current_map)
                add_file_to_open_dir(dir_stack[-1], basename, disk_size, top_n)

        elif event == "end_array":
            # End of directory. All children have been parsed. We can remove from stack,
            # and collapse children
            if dir_stack:
                finished = finalize_open_dir(dir_stack.pop())
                if dir_stack:
                    parent = dir_stack[-1]
                    order = parent.next_child_order
                    parent.next_child_order += 1
                    parent.dir_children.append((order, finished))
                    parent.node.total_bytes += finished.total_bytes
                    parent.node.total_files += finished.total_files
                    parent.node.total_directories += finished.total_directories
                    parent.node.uncompressed.add_to_self(finished.uncompressed)
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
    if not isinstance(raw_name, str) or raw_name == "":
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
