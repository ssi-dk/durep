from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


NcduEntry = NcduDir | NcduFile


def path_str(node: NcduEntry) -> str:
    parts: list[str] = []
    current: NcduEntry = node
    while True:
        parts.append(current.basename)
        if isinstance(current, NcduFile):
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
    timestamp: datetime | None = None


def parse_ncdu_json_file(path: Path) -> NcduRun:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    error_prefix = f"Invalid NCDU JSON file at {str(path)}: "

    # Per NCDU format, the JSON object is a list with the first two fields
    # being major and minor versions. Check the version, because otherwise
    # we don't know how to parse it.
    if not (isinstance(data, list) and len(data) > 3 and isinstance(data[0], int) and data[0] == 1):
        raise ValueError(error_prefix + "NCDU JSON file is not a valid version 1 NCDU format file")
    else:
        # More fields could be added in a minor version so only check first four
        (_, _, metadata, root) = data[:4]

    # Extract timestamp
    if not (isinstance(metadata, dict) and ("timestamp" in metadata)):
        raise ValueError(error_prefix + "Does not contain expected timestamp field in metadata")

    try:
        timestamp = datetime.fromtimestamp(int(metadata["timestamp"]), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(error_prefix + "could not parse timestamp as POSIX timestamp")

    if not is_dir_tree(root):
        raise ValueError("Fourth field (root directory) is not a valid directory in NCDU format")

    return NcduRun(root=parse_dir_tree(root, parent=None), timestamp=timestamp)


def is_dir_tree(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], dict)
        and "name" in value[0]
    )


def parse_dir_tree(tree: list[Any], parent: NcduDir | None) -> NcduDir:
    # First entry in a dir is the dir itself
    metadata = tree[0]
    if not isinstance(metadata, dict):
        raise ValueError("Directory tree metadata must be a JSON object")

    basename = get_required_name(metadata)
    if parent is None:
        # Root node must be absolute
        if not Path(basename).is_absolute():
            raise ValueError(f"Root node path must be absolute, got: {basename}")

    disk_size = parse_disk_size(metadata)

    # Create directory node with placeholders, then fill in children + aggregates
    node = NcduDir(
        basename=basename,
        parent=parent,
        disk_size=disk_size,
        total_bytes=0,
        total_files=0,
        total_directories=0,
        uncompressed=UncompressedStats.zero(),
    )

    # Subsequent entries in a dir list is its direct children
    for child in tree[1:]:
        if isinstance(child, dict):
            node.children.append(parse_file_entry(child, parent=node))
            continue
        if is_dir_tree(child):
            node.children.append(parse_dir_tree(child, parent=node))

    # Since these are computed recursively already, this pass here only need to
    # touch the top level subdirectories, and so will be fast
    node.total_bytes = disk_size + sum(child.total_bytes for child in node.children)
    node.total_files = sum(c.total_files if isinstance(c, NcduDir) else 1 for c in node.children)
    node.total_directories = 1 + sum(
        c.total_directories for c in node.children if isinstance(c, NcduDir)
    )
    agg = UncompressedStats.zero()
    for child in node.children:
        agg.fasta += child.uncompressed.fasta
        agg.fastq += child.uncompressed.fastq
        agg.vcf += child.uncompressed.vcf
        agg.sam += child.uncompressed.sam
    node.uncompressed = agg
    return node


def parse_file_entry(entry: dict[str, Any], parent: NcduDir) -> NcduFile:
    basename = get_required_name(entry)
    disk_size = parse_disk_size(entry)
    return NcduFile(
        basename=basename,
        parent=parent,
        disk_size=disk_size,
        uncompressed=UncompressedStats.from_file_node(basename, disk_size),
    )


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
