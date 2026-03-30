from __future__ import annotations

import logging
from pathlib import Path

import pytest

from durep.metadata import Owner, ProjectName, load_project_owners, resolve_project_owners


def write_csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


PN = ProjectName
OW = Owner


def test_load_valid_csv(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path / "meta.csv",
        "project,group\nproj_a,Alice\nproj_b,Bob\n",
    )
    result = load_project_owners(csv)
    assert result == {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("Bob")}


def test_load_csv_strips_whitespace(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path / "meta.csv",
        "project,group\n  proj_a , Alice \n",
    )
    result = load_project_owners(csv)
    assert result == {PN("proj_a"): OW("Alice")}


def test_load_csv_empty_owner_returns_none(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path / "meta.csv",
        "project,group\nproj_a,\n",
    )
    result = load_project_owners(csv)
    assert result == {PN("proj_a"): None}


def test_load_csv_missing_owner_field_in_row(tmp_path: Path) -> None:
    """A row with fewer columns than the header yields None from DictReader; should not crash."""
    csv = write_csv(
        tmp_path / "meta.csv",
        "project,group,Extra\nproj_a\n",
    )
    result = load_project_owners(csv)
    assert result == {PN("proj_a"): None}


def test_load_csv_missing_display_name_column(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path / "meta.csv",
        "Name,group\nproj_a,Alice\n",
    )
    with pytest.raises(ValueError, match="missing required column.*project"):
        load_project_owners(csv)


def test_load_csv_missing_legal_owner_column(tmp_path: Path) -> None:
    csv = write_csv(
        tmp_path / "meta.csv",
        "project,Owner\nproj_a,Alice\n",
    )
    with pytest.raises(ValueError, match="missing required column.*group"):
        load_project_owners(csv)


def test_load_csv_empty_file(tmp_path: Path) -> None:
    csv = write_csv(tmp_path / "meta.csv", "")
    with pytest.raises(ValueError, match="empty or has no header"):
        load_project_owners(csv)


def test_resolve_all_present() -> None:
    csv_owners: dict[ProjectName, Owner | None] = {
        PN("proj_a"): OW("Alice"),
        PN("proj_b"): OW("Bob"),
    }
    result = resolve_project_owners([PN("proj_a"), PN("proj_b")], csv_owners)
    assert result == {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("Bob")}


def test_resolve_missing_project_warns(caplog: pytest.LogCaptureFixture) -> None:
    csv_owners: dict[ProjectName, Owner | None] = {PN("proj_a"): OW("Alice")}
    with caplog.at_level(logging.WARNING, logger="durep"):
        result = resolve_project_owners([PN("proj_a"), PN("proj_b")], csv_owners)
    assert result == {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("proj_b")}
    assert "proj_b" in caplog.text
    assert "no legal owner" in caplog.text


def test_resolve_none_owner_warns(caplog: pytest.LogCaptureFixture) -> None:
    csv_owners: dict[ProjectName, Owner | None] = {PN("proj_a"): OW("Alice"), PN("proj_b"): None}
    with caplog.at_level(logging.WARNING, logger="durep"):
        result = resolve_project_owners([PN("proj_a"), PN("proj_b")], csv_owners)
    assert result == {PN("proj_a"): OW("Alice"), PN("proj_b"): OW("proj_b")}
    assert "proj_b" in caplog.text


def test_resolve_extra_csv_entries_ignored() -> None:
    csv_owners: dict[ProjectName, Owner | None] = {
        PN("proj_a"): OW("Alice"),
        PN("proj_c"): OW("Charlie"),
    }
    result = resolve_project_owners([PN("proj_a")], csv_owners)
    assert result == {PN("proj_a"): OW("Alice")}
