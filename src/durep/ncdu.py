from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Literal


class ParsedSizes(NamedTuple):
    apparent_size: int
    disk_size: int


@dataclass(slots=True)
class NcduNode:
    # Absolute path: root must be absolute, children are resolved from their parent
    path: Path
    node_type: Literal["dir", "file"]
    apparent_size: int
    disk_size: int
    file_count: int
    total_bytes: int
    total_files: int
    total_directories: int
    children: list[NcduNode] = field(default_factory=list)


@dataclass(slots=True)
class NcduRun:
    root: NcduNode
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

    return NcduRun(root=parse_dir_tree(root, parent_path=None), timestamp=timestamp)


def is_dir_tree(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], dict)
        and "name" in value[0]
    )


def parse_dir_tree(tree: list[Any], parent_path: Path | None) -> NcduNode:
    # First entry in a dir is the dir itself
    metadata = tree[0]
    if not isinstance(metadata, dict):
        raise ValueError("Directory tree metadata must be a JSON object")

    node_path = resolve_child_path(parent_path=parent_path, name=get_required_name(metadata))
    sizes = parse_sizes(metadata)

    # Subsequent entries in a dir list is its direct children
    children: list[NcduNode] = []
    for child in tree[1:]:
        if isinstance(child, dict):
            children.append(parse_file_entry(child, parent_path=node_path))
            continue
        if is_dir_tree(child):
            children.append(parse_dir_tree(child, parent_path=node_path))

    # Since these are computed recursively already, this pass here only need to
    # touch the top level subdirectories, and so will be fast
    total_bytes = sizes.disk_size + sum(child.total_bytes for child in children)
    total_files = sum(child.total_files for child in children)
    total_directories = 1 + sum(child.total_directories for child in children)
    return NcduNode(
        path=node_path,
        node_type="dir",
        apparent_size=sizes.apparent_size,
        disk_size=sizes.disk_size,
        file_count=0,
        total_bytes=total_bytes,
        total_files=total_files,
        total_directories=total_directories,
        children=children,
    )


def parse_file_entry(entry: dict[str, Any], parent_path: Path) -> NcduNode:
    name = get_required_name(entry)
    node_path = resolve_child_path(parent_path=parent_path, name=name)
    sizes = parse_sizes(entry)
    return NcduNode(
        path=node_path,
        node_type="file",
        apparent_size=sizes.apparent_size,
        disk_size=sizes.disk_size,
        file_count=1,
        total_bytes=sizes.disk_size,
        total_files=1,
        total_directories=0,
        children=[],
    )


def parse_sizes(entry: dict[str, Any]) -> ParsedSizes:
    apparent_size = parse_non_negative_int(entry.get("asize", 0), field_name="asize")
    disk_size = parse_non_negative_int(entry.get("dsize", 0), field_name="dsize")
    return ParsedSizes(apparent_size=apparent_size, disk_size=disk_size)


def get_required_name(entry: dict[str, Any]) -> str:
    raw_name = entry.get("name")
    if not isinstance(raw_name, str) or raw_name.strip() == "":
        raise ValueError("Each ncdu entry must include a non-empty string 'name'")
    return raw_name


def resolve_child_path(parent_path: Path | None, name: str) -> Path:
    if parent_path is None:
        root_path = Path(name)
        # Root node must be absolute, else the user may not know which directory is
        # referred to, as they don't know where NCDU was run from
        if root_path.is_absolute():
            return root_path
        raise ValueError(f"Root node path must be absolute, got: {name}")
    return parent_path / name


def parse_non_negative_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer value for {field_name}: {value}") from exc
    if parsed < 0:
        raise ValueError(f"Negative values are not allowed for {field_name}: {parsed}")
    return parsed
