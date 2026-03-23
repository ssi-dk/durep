from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from durep.ncdu import parse_ncdu_json_file


def write_ncdu_json(path: Path, root: list[object], timestamp: int = 1700000000) -> None:
    payload = [1, 2, {"progname": "ncdu", "timestamp": timestamp}, root]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parse_ncdu_json_file_builds_normalized_tree_with_aggregates(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/", "asize": 100, "dsize": 120},
        {"name": "file_a.bin", "asize": 10, "dsize": 12},
        [
            {"name": "dir1", "asize": 5},
            {"name": "nested.txt", "asize": 7, "dsize": 8},
            [
                {"name": "dir2", "asize": 2, "dsize": 3},
                {"name": "deep.bin", "asize": 1},
            ],
        ],
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source)

    root = run.root

    assert root.path == Path("/")
    assert root.node_type == "dir"
    assert root.disk_size == 120
    assert root.total_bytes == 143
    assert root.total_files == 3
    assert root.total_directories == 3

    file_a = root.children[0]
    assert file_a.path == Path("/file_a.bin")
    assert file_a.node_type == "file"
    assert file_a.disk_size == 12
    assert file_a.total_files == 1
    assert file_a.total_directories == 0

    dir1 = root.children[1]
    assert dir1.path == Path("/dir1")
    assert dir1.node_type == "dir"
    assert dir1.disk_size == 0
    assert dir1.total_bytes == 11
    assert dir1.total_files == 2
    assert dir1.total_directories == 2

    dir2 = dir1.children[1]
    assert dir2.path == Path("/dir1/dir2")
    assert dir2.node_type == "dir"
    assert dir2.total_bytes == 3
    assert dir2.total_files == 1
    assert dir2.total_directories == 1

    deep = dir2.children[0]
    assert deep.path == Path("/dir1/dir2/deep.bin")
    assert deep.disk_size == 0


def test_parse_ncdu_json_file_extracts_timestamp(tmp_path: Path) -> None:
    root_tree = [{"name": "/", "asize": 1}]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree, timestamp=1700000000)
    run = parse_ncdu_json_file(source)

    assert run.timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_parse_ncdu_json_file_rejects_missing_timestamp(tmp_path: Path) -> None:
    payload = [1, 2, {"progname": "ncdu"}, [{"name": "/", "asize": 1}]]

    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Does not contain expected timestamp field in metadata"):
        parse_ncdu_json_file(source)


def test_parse_ncdu_json_file_rejects_relative_root_name(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, [{"name": "tmp", "asize": 1}])

    with pytest.raises(ValueError, match="Root node path must be absolute"):
        parse_ncdu_json_file(source)


def test_parse_ncdu_json_file_reads_json(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, [{"name": "/", "asize": 1}])

    run = parse_ncdu_json_file(source)
    assert run.root.path == Path("/")
    assert run.root.total_bytes == 0


def test_parse_ncdu_json_file_rejects_invalid_shape(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    source.write_text('{"name": "/"}', encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid version 1 NCDU format file"):
        parse_ncdu_json_file(source)
