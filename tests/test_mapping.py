from __future__ import annotations

from pathlib import Path

import pytest

from durep.mapping import MappingRule, UNASSIGNED_PROJECT, assign_project, parse_mapping_csv_file


def test_parse_mapping_csv_file_reads_rules(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "directory,project\n/data/project-a,project_a\n/data/project-b,project_b\n",
        encoding="utf-8",
    )

    rules = parse_mapping_csv_file(mapping)

    assert len(rules) == 2
    assert rules[0].directory == Path("/data/project-a")
    assert rules[0].project == "project_a"


def test_parse_mapping_csv_file_rejects_invalid_header(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.csv"
    mapping.write_text("directory,project,user\n", encoding="utf-8")

    with pytest.raises(ValueError, match="header must be exactly"):
        parse_mapping_csv_file(mapping)


def test_assign_project_uses_longest_prefix_and_falls_back_to_unassigned() -> None:
    rules = [
        MappingRule(directory=Path("/data"), project="top"),
        MappingRule(directory=Path("/data/project-a"), project="project_a"),
    ]

    assert assign_project(Path("/data/project-a/sub/file.txt"), rules) == "project_a"
    assert assign_project(Path("/tmp/no-owner.txt"), rules) == UNASSIGNED_PROJECT
