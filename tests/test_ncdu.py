from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collections.abc import Sequence

from durep.analytics import extract_project_sample
from durep.ncdu import (
    CollapsedNode,
    NcduDir,
    NcduFile,
    full_path,
    parse_ncdu_json_file,
    parse_ncdu_project_sample,
)


def write_ncdu_json(path: Path, root: Sequence[object], timestamp: int = 1700000000) -> None:
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

    assert isinstance(root, NcduDir)
    assert full_path(root) == Path("/")
    assert root.disk_size == 120
    assert root.total_bytes == 143
    assert root.total_files == 3
    assert root.total_directories == 3

    file_a = root.children[0]
    assert isinstance(file_a, NcduFile)
    assert full_path(file_a) == Path("/file_a.bin")
    assert file_a.disk_size == 12

    dir1 = root.children[1]
    assert isinstance(dir1, NcduDir)
    assert full_path(dir1) == Path("/dir1")
    assert dir1.disk_size == 0
    assert dir1.total_bytes == 11
    assert dir1.total_files == 2
    assert dir1.total_directories == 2

    dir2 = dir1.children[1]
    assert isinstance(dir2, NcduDir)
    assert full_path(dir2) == Path("/dir1/dir2")
    assert dir2.total_bytes == 3
    assert dir2.total_files == 1
    assert dir2.total_directories == 1

    deep = dir2.children[0]
    assert isinstance(deep, NcduFile)
    assert full_path(deep) == Path("/dir1/dir2/deep.bin")
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
    assert full_path(run.root) == Path("/")
    assert run.root.total_bytes == 0


def test_parse_ncdu_json_file_rejects_invalid_shape(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    source.write_text('{"name": "/"}', encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid version 1 NCDU format file"):
        parse_ncdu_json_file(source)


def test_parse_ncdu_json_file_rejects_truncated_json(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    source.write_text("[1]", encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid version 1 NCDU format file"):
        parse_ncdu_json_file(source)


def test_parse_ncdu_json_file_empty_directory(tmp_path: Path) -> None:
    root_tree = [{"name": "/", "dsize": 50}]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source)

    assert run.root.disk_size == 50
    assert run.root.total_bytes == 50
    assert run.root.total_files == 0
    assert run.root.total_directories == 1
    assert run.root.children == []


def test_parse_ncdu_json_file_directory_only_children(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/", "dsize": 10},
        [
            {"name": "sub1", "dsize": 5},
            [{"name": "sub1a", "dsize": 2}],
        ],
        [{"name": "sub2", "dsize": 7}],
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source)

    root = run.root
    assert root.total_files == 0
    assert root.total_directories == 4
    assert root.total_bytes == 24
    assert len(root.children) == 2
    assert all(isinstance(c, NcduDir) for c in root.children)


def test_parse_ncdu_project_sample_matches_tree_parser_for_nested_tree(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/proj", "dsize": 10},
        {"name": "reads.fastq", "dsize": 100},
        [
            {"name": "sub", "dsize": 5},
            {"name": "genome.fasta", "dsize": 30},
            [{"name": "deep", "dsize": 2}, {"name": "align.sam", "dsize": 40}],
        ],
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree, timestamp=1700000000)

    lazy_sample = parse_ncdu_project_sample(source)
    tree_sample = extract_project_sample(parse_ncdu_json_file(source))

    assert lazy_sample == tree_sample


def test_parse_ncdu_project_sample_matches_tree_parser_for_directory_only_tree(
    tmp_path: Path,
) -> None:
    root_tree = [
        {"name": "/proj", "dsize": 10},
        [{"name": "sub1", "dsize": 5}],
        [{"name": "sub2", "dsize": 7}],
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree, timestamp=1700000000)

    lazy_sample = parse_ncdu_project_sample(source)
    tree_sample = extract_project_sample(parse_ncdu_json_file(source))

    assert lazy_sample == tree_sample


def test_parse_ncdu_project_sample_rejects_relative_root_name(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, [{"name": "tmp", "asize": 1}])

    with pytest.raises(ValueError, match="Root node path must be absolute"):
        parse_ncdu_project_sample(source)


def test_parse_ncdu_project_sample_rejects_missing_timestamp(tmp_path: Path) -> None:
    payload = [1, 2, {"progname": "ncdu"}, [{"name": "/", "asize": 1}]]

    source = tmp_path / "snapshot.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Does not contain expected timestamp field in metadata"):
        parse_ncdu_project_sample(source)


def test_parse_ncdu_project_sample_rejects_truncated_json(tmp_path: Path) -> None:
    source = tmp_path / "snapshot.json"
    source.write_text("[1]", encoding="utf-8")

    with pytest.raises(ValueError, match="not a valid version 1 NCDU format file"):
        parse_ncdu_project_sample(source)


def test_parse_with_top_n_collapses_excess_children(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/", "dsize": 0},
        {"name": "big.bin", "dsize": 500},
        {"name": "medium.bin", "dsize": 300},
        {"name": "small1.bin", "dsize": 100},
        {"name": "small2.bin", "dsize": 50},
        {"name": "small3.bin", "dsize": 10},
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source, top_n=2)

    root = run.root
    # 2 kept + 1 collapsed node = 3 children
    assert len(root.children) == 3
    assert isinstance(root.children[0], NcduFile)
    assert root.children[0].basename == "big.bin"
    assert isinstance(root.children[1], NcduFile)
    assert root.children[1].basename == "medium.bin"

    collapsed = root.children[2]
    assert isinstance(collapsed, CollapsedNode)
    assert collapsed.count == 3
    assert collapsed.total_bytes == 160  # 100 + 50 + 10

    # Aggregates should be preserved
    assert root.total_bytes == 960
    assert root.total_files == 5


def test_parse_with_top_n_none_does_not_collapse(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/", "dsize": 0},
        {"name": "a.bin", "dsize": 10},
        {"name": "b.bin", "dsize": 20},
        {"name": "c.bin", "dsize": 30},
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source, top_n=None)

    assert len(run.root.children) == 3
    assert all(isinstance(c, NcduFile) for c in run.root.children)


def test_parse_with_top_n_preserves_uncompressed_stats(tmp_path: Path) -> None:
    root_tree = [
        {"name": "/", "dsize": 0},
        {"name": "big.fastq", "dsize": 500},
        {"name": "small1.fasta", "dsize": 100},
        {"name": "small2.sam", "dsize": 50},
    ]

    source = tmp_path / "snapshot.json"
    write_ncdu_json(source, root_tree)
    run = parse_ncdu_json_file(source, top_n=1)

    root = run.root
    assert len(root.children) == 2  # 1 kept + 1 collapsed

    collapsed = root.children[1]
    assert isinstance(collapsed, CollapsedNode)
    assert collapsed.uncompressed.fasta == 100
    assert collapsed.uncompressed.sam == 50

    # Root uncompressed totals still correct
    assert root.uncompressed.fastq == 500
    assert root.uncompressed.fasta == 100
    assert root.uncompressed.sam == 50
