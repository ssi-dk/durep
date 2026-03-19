from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from durep.ncdu import parse_ncdu_json_data, parse_ncdu_json_file


def test_parse_ncdu_json_data_builds_normalized_tree_with_aggregates() -> None:
    data = [
        1,
        2,
        {"progname": "ncdu"},
        [
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
        ],
    ]

    run = parse_ncdu_json_data(data)
    root = run.root

    assert root.path == Path("/")
    assert root.node_type == "dir"
    assert root.apparent_size == 100
    assert root.disk_size == 120
    assert root.total_bytes == 149
    assert root.total_files == 3
    assert root.total_directories == 3

    file_a = root.children[0]
    assert file_a.path == Path("/file_a.bin")
    assert file_a.node_type == "file"
    assert file_a.file_count == 1
    assert file_a.disk_size == 12
    assert file_a.total_files == 1
    assert file_a.total_directories == 0

    dir1 = root.children[1]
    assert dir1.path == Path("/dir1")
    assert dir1.node_type == "dir"
    assert dir1.disk_size == 5
    assert dir1.total_bytes == 17
    assert dir1.total_files == 2
    assert dir1.total_directories == 2

    dir2 = dir1.children[1]
    assert dir2.path == Path("/dir1/dir2")
    assert dir2.node_type == "dir"
    assert dir2.total_bytes == 4
    assert dir2.total_files == 1
    assert dir2.total_directories == 1

    deep = dir2.children[0]
    assert deep.path == Path("/dir1/dir2/deep.bin")
    assert deep.disk_size == 1
    assert deep.apparent_size == 1


def test_parse_ncdu_json_data_extracts_timestamp() -> None:
    data = [
        1,
        2,
        {"progname": "ncdu", "progver": "2.7", "timestamp": 1700000000},
        [{"name": "/", "asize": 1}],
    ]

    run = parse_ncdu_json_data(data)
    assert run.timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_parse_ncdu_json_data_timestamp_is_none_when_missing() -> None:
    run = parse_ncdu_json_data([{"name": "/", "asize": 1}])
    assert run.timestamp is None


def test_parse_ncdu_json_data_rejects_relative_root_name() -> None:
    with pytest.raises(ValueError, match="Root node path must be absolute"):
        parse_ncdu_json_data([{"name": "tmp", "asize": 1}])


def test_parse_ncdu_json_file_reads_json(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    source.write_text('[{"name": "/", "asize": 1}]', encoding="utf-8")

    run = parse_ncdu_json_file(source)
    assert run.root.path == Path("/")
    assert run.root.total_bytes == 1


def test_parse_ncdu_json_data_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="Unable to locate an ncdu directory tree"):
        parse_ncdu_json_data({"name": "/"})
