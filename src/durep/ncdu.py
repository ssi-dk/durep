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
    def from_total_size(cls, total_size: int) -> UncompressedStats:
        return cls(0, 0, 0, 0, total_size)

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

    @property
    def total_bytes(self) -> int:
        return self.disk_size

    @property
    def uncompressed(self) -> UncompressedStats:
        return UncompressedStats.from_file_node(self.basename, self.disk_size)


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
    total_bytes: int
    uncompressed_bytes: int


NcduEntry = NcduDir | NcduFile | CollapsedNode


@dataclass(slots=True)
class OpenDirState:
    """Mutable state for a directory whose children are still being parsed."""

    node: NcduDir
    # Monotonic counter assigning each child a unique key. Used as the dict
    # key in dir_children (enabling O(1) replacement during inline collapsing)
    # and as a tiebreaker in the kept_files min-heap.
    next_child_order: int = 0
    # Finalized child directories, keyed by their order. A dict rather than a
    # list so that collapse_dir_inline can replace an entry in O(1).
    dir_children: dict[int, NcduEntry] = field(default_factory=dict)
    # Min-heap of (disk_size, order, NcduFile, uncompressed_total) keeping the top_n largest files.
    kept_files: list[tuple[int, int, NcduFile, int]] = field(default_factory=list)
    # Aggregate stats for files evicted from kept_files, materialized as a
    # single CollapsedNode in finalize_open_dir.
    collapsed_count: int = 0
    collapsed_total_bytes: int = 0
    collapsed_uncompressed_bytes: int = 0


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

    def to_project_sample(self) -> "ProjectSample":
        from durep.analytics import ProjectSample

        root_str = path_str(self.root)
        project = root_str.rsplit("/", 1)[-1] or root_str
        return ProjectSample(
            project=project,
            timestamp=self.timestamp,
            date=self.timestamp.date(),
            total_bytes=self.root.total_bytes,
            total_files=self.root.total_files,
            total_directories=self.root.total_directories,
            uncompressed=self.root.uncompressed,
        )


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

    root = parse_tree_streaming(parser, top_n=top_n, dir_budget=display_nodes)
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

    total_bytes = 0
    uncompressed_bytes = 0
    for c in to_collapse:
        total_bytes += c.total_bytes
        uncompressed_bytes += c.uncompressed.total_size

    collapsed = CollapsedNode(
        basename=f"({len(to_collapse)} collapsed entries)",
        parent=directory,
        count=len(to_collapse),
        total_bytes=total_bytes,
        uncompressed_bytes=uncompressed_bytes,
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
                            total_bytes=child.total_bytes,
                            uncompressed_bytes=child.uncompressed.total_size,
                        )
                    )
            else:
                new_children.append(child)
        node.children = new_children


def parse_tree_streaming(
    parser: Iterator[Event], top_n: int = 20, dir_budget: int = 5000
) -> NcduDir:
    # Hoist frequently-used callables to locals to avoid repeated global/attribute lookups
    _heappush = heapq.heappush
    _heapreplace = heapq.heapreplace
    _heappushpop = heapq.heappushpop
    _heapify = heapq.heapify
    _ext_to_fmt = EXTENSION_TO_FORMAT
    _id = id
    _len = len

    dir_stack: list[OpenDirState] = []
    dir_stack_append = dir_stack.append
    dir_stack_pop = dir_stack.pop
    awaiting_dir_metadata = False
    in_map = False
    is_heap = False
    map_key = ""
    map_name = ""
    map_dsize = 0
    root: NcduDir | None = None

    # Inline directory collapsing state
    dir_heap: list[
        tuple[int, int, int, int, NcduDir]
    ] = []  # (total_bytes, -depth, counter, order, node)
    heap_counter = 0
    open_state_map: dict[int, OpenDirState] = {}
    finalized_child_index: dict[int, int] = {}  # id(dir) -> index in parent.children

    for event, value in parser:
        # Branch order optimized: ~22M of ~29M events occur inside maps,
        # so check in_map first.  Within a map, value events (~11M) are more
        # frequent than map_key (~5.5M) or end_map (~5.5M).
        if in_map:
            if event == "map_key":
                map_key = value

            elif event == "end_map":
                in_map = False
                name = map_name
                dsize = map_dsize

                if awaiting_dir_metadata:
                    awaiting_dir_metadata = False
                    if not name:
                        raise ValueError("Each ncdu entry must include a non-empty string 'name'")
                    parent = dir_stack[-1].node if dir_stack else None
                    node = NcduDir(
                        basename=name,
                        parent=parent,
                        disk_size=dsize,
                        total_bytes=dsize,
                        total_files=0,
                        total_directories=1,
                        uncompressed=UncompressedStats(0, 0, 0, 0, 0),
                    )
                    state = OpenDirState(node=node)
                    dir_stack_append(state)
                    open_state_map[_id(node)] = state
                else:
                    # --- inlined add_file_to_open_dir ---
                    if not name:
                        raise ValueError("Each ncdu entry must include a non-empty string 'name'")
                    directory = dir_stack[-1]
                    dir_node = directory.node

                    dir_node.total_bytes += dsize
                    dir_node.total_files += 1

                    # Inlined from_file_node: compute uncompressed category
                    # without allocating an UncompressedStats per file.
                    dot = name.rfind(".")
                    fmt = _ext_to_fmt.get(name[dot + 1 :]) if dot >= 0 else None

                    if fmt is not None:
                        unc = dir_node.uncompressed
                        if fmt == "fasta":
                            unc.fasta += dsize
                        elif fmt == "fastq":
                            unc.fastq += dsize
                        elif fmt == "vcf":
                            unc.vcf += dsize
                        elif fmt == "sam":
                            unc.sam += dsize
                        else:
                            unc.other += dsize
                        file_unc_total = dsize
                    else:
                        file_unc_total = 0

                    order = directory.next_child_order
                    directory.next_child_order += 1
                    kept = directory.kept_files

                    if _len(kept) < top_n:
                        _heappush(
                            kept,
                            (
                                dsize,
                                order,
                                NcduFile(basename=name, parent=dir_node, disk_size=dsize),
                                file_unc_total,
                            ),
                        )
                    elif dsize > kept[0][0]:
                        _ev_bytes, _ev_ord, _ev_file, ev_unc = _heapreplace(
                            kept,
                            (
                                dsize,
                                order,
                                NcduFile(basename=name, parent=dir_node, disk_size=dsize),
                                file_unc_total,
                            ),
                        )
                        directory.collapsed_count += 1
                        directory.collapsed_total_bytes += _ev_bytes
                        directory.collapsed_uncompressed_bytes += ev_unc
                    else:
                        directory.collapsed_count += 1
                        directory.collapsed_total_bytes += dsize
                        directory.collapsed_uncompressed_bytes += file_unc_total

            else:
                # Value event inside a map — only capture name and dsize
                if map_key == "name":
                    map_name = value
                elif map_key == "dsize":
                    map_dsize = value  # ijson yajl2_c already returns int

        elif event == "start_map":
            in_map = True
            map_name = ""
            map_dsize = 0

        elif event == "start_array":
            awaiting_dir_metadata = True

        elif event == "end_array":
            if dir_stack:
                popped_state = dir_stack_pop()
                popped_node = popped_state.node
                open_state_map.pop(_id(popped_node), None)

                # --- inlined finalize_open_dir ---
                children: list[tuple[int, NcduEntry]] = []
                children.extend(popped_state.dir_children.items())
                children.extend((o, f) for _b, o, f, _u in popped_state.kept_files)
                children.sort(key=lambda c: c[0])
                popped_node.children = new_children = [c for _o, c in children]

                for i, child in enumerate(new_children):
                    if isinstance(child, NcduDir):
                        finalized_child_index[_id(child)] = i

                if popped_state.collapsed_count > 0:
                    new_children.append(
                        CollapsedNode(
                            basename=f"({popped_state.collapsed_count} collapsed entries)",
                            parent=popped_node,
                            count=popped_state.collapsed_count,
                            total_bytes=popped_state.collapsed_total_bytes,
                            uncompressed_bytes=popped_state.collapsed_uncompressed_bytes,
                        )
                    )
                finished = popped_node

                if dir_stack:
                    parent_state = dir_stack[-1]
                    order = parent_state.next_child_order
                    parent_state.next_child_order += 1
                    parent_state.dir_children[order] = finished
                    parent_node = parent_state.node
                    parent_node.total_bytes += finished.total_bytes
                    parent_node.total_files += finished.total_files
                    parent_node.total_directories += finished.total_directories
                    parent_node.uncompressed.add_to_self(finished.uncompressed)

                    # Push onto dir_heap for potential inline collapsing
                    depth = _len(dir_stack)
                    heap_element = (
                        finished.total_bytes,
                        -depth,
                        heap_counter,
                        order,
                        finished,
                    )
                    heap_counter += 1
                    if is_heap:
                        _bytes, _neg_depth, _cnt, victim_order, victim = _heappushpop(
                            dir_heap, heap_element
                        )
                        # --- inlined collapse_dir_inline ---
                        victim_parent = victim.parent
                        assert victim_parent is not None
                        replacement = CollapsedNode(
                            basename=victim.basename,
                            parent=victim_parent,
                            count=victim.total_files + victim.total_directories,
                            total_bytes=victim.total_bytes,
                            uncompressed_bytes=victim.uncompressed.total_size,
                        )
                        vp_state = open_state_map.get(_id(victim_parent))
                        if vp_state is not None:
                            vp_state.dir_children[victim_order] = replacement
                        else:
                            idx = finalized_child_index.pop(_id(victim))
                            victim_parent.children[idx] = replacement
                        victim.children.clear()
                    else:
                        dir_heap.append(heap_element)
                        if _len(dir_heap) >= dir_budget:
                            _heapify(dir_heap)
                            is_heap = True
                else:
                    root = finished

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
