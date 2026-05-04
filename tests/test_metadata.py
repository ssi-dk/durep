from __future__ import annotations

import logging
from pathlib import Path

import pytest

from durep.metadata import (
    Owner,
    ProjectLead,
    ProjectMetadata,
    ProjectName,
    load_project_metadata,
    resolve_project_metadata,
)


def write_tsv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


PN = ProjectName
OW = Owner
PL = ProjectLead


def test_load_valid_tsv(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\tproject_lead\nproj_a\tAlice\tCarol, Dan\nproj_b\tBob\tEve\n",
    )

    result = load_project_metadata(tsv)

    assert result == {
        PN("proj_a"): ProjectMetadata(OW("Alice"), (PL("Carol"), PL("Dan"))),
        PN("proj_b"): ProjectMetadata(OW("Bob"), (PL("Eve"),)),
    }


def test_load_tsv_strips_whitespace(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\tproject_lead\n  proj_a \t Alice \t Carol \n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"), (PL("Carol"),))}


def test_load_tsv_empty_owner_returns_none(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\tproject_lead\nproj_a\t\t\n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(None, ())}


def test_load_tsv_missing_optional_lead_field_in_row(tmp_path: Path) -> None:
    """A row with fewer columns than the header yields None from DictReader; should not crash."""
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\tproject_lead\nproj_a\tAlice\n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"), ())}


def test_load_tsv_missing_display_name_column(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "name\tlegal_owner\tproject_lead\nproj_a\tAlice\tCarol\n",
    )
    with pytest.raises(ValueError, match="missing required column.*project"):
        load_project_metadata(tsv)


def test_load_tsv_missing_legal_owner_column(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tproject_lead\nproj_a\tCarol\n",
    )
    with pytest.raises(ValueError, match="missing required column.*legal_owner"):
        load_project_metadata(tsv)


def test_load_tsv_rejects_project_leads_column(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\tproject_leads\nproj_a\tAlice\tCarol\n",
    )
    with pytest.raises(ValueError, match="missing required column.*project_lead"):
        load_project_metadata(tsv)


def test_load_tsv_empty_file(tmp_path: Path) -> None:
    tsv = write_tsv(tmp_path / "meta.tsv", "")
    with pytest.raises(ValueError, match="empty or has no header"):
        load_project_metadata(tsv)


def test_resolve_all_present() -> None:
    tsv_metadata = {
        PN("proj_a"): ProjectMetadata(OW("Alice"), (PL("Carol"),)),
        PN("proj_b"): ProjectMetadata(OW("Bob"), ()),
    }

    result = resolve_project_metadata([PN("proj_a"), PN("proj_b")], tsv_metadata)

    assert result == tsv_metadata


def test_resolve_missing_project_warns(caplog: pytest.LogCaptureFixture) -> None:
    tsv_metadata = {PN("proj_a"): ProjectMetadata(OW("Alice"), ())}

    with caplog.at_level(logging.WARNING, logger="durep"):
        result = resolve_project_metadata([PN("proj_a"), PN("proj_b")], tsv_metadata)

    assert result == {
        PN("proj_a"): ProjectMetadata(OW("Alice"), ()),
        PN("proj_b"): ProjectMetadata(None, ()),
    }
    assert "proj_b" in caplog.text
    assert "no metadata" in caplog.text


def test_resolve_extra_tsv_entries_ignored() -> None:
    tsv_metadata = {
        PN("proj_a"): ProjectMetadata(OW("Alice"), ()),
        PN("proj_c"): ProjectMetadata(OW("Charlie"), ()),
    }

    result = resolve_project_metadata([PN("proj_a")], tsv_metadata)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"), ())}
