from __future__ import annotations

import logging
from pathlib import Path

import pytest

from durep.metadata import (
    Owner,
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


def test_load_valid_tsv(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\nproj_a\tAlice\nproj_b\tBob\n",
    )

    result = load_project_metadata(tsv)

    assert result == {
        PN("proj_a"): ProjectMetadata(OW("Alice")),
        PN("proj_b"): ProjectMetadata(OW("Bob")),
    }


def test_load_tsv_strips_whitespace(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\n  proj_a \t Alice \n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"))}


def test_load_tsv_empty_owner_returns_none(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\nproj_a\t\n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(None)}


def test_load_tsv_ignores_empty_and_whitespace_only_rows(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\n\n  \n\t\nproj_a\tAlice\n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"))}


def test_load_tsv_rejects_nonempty_row_without_project(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\n\tAlice\n",
    )

    with pytest.raises(ValueError, match="non-empty row without a project"):
        load_project_metadata(tsv)


def test_load_tsv_rejects_duplicate_project(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\nproj_a\tAlice\n proj_a \tBob\n",
    )

    with pytest.raises(ValueError, match="duplicate project.*proj_a"):
        load_project_metadata(tsv)


def test_load_tsv_missing_owner_field_in_row(tmp_path: Path) -> None:
    """A row with fewer columns than the header yields None from DictReader; should not crash."""
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\tlegal_owner\nproj_a\n",
    )

    result = load_project_metadata(tsv)

    assert result == {PN("proj_a"): ProjectMetadata(None)}


def test_load_tsv_missing_display_name_column(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "name\tlegal_owner\nproj_a\tAlice\n",
    )
    with pytest.raises(ValueError, match="missing required column.*project"):
        load_project_metadata(tsv)


def test_load_tsv_missing_legal_owner_column(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "project\nproj_a\n",
    )
    with pytest.raises(ValueError, match="missing required column.*legal_owner"):
        load_project_metadata(tsv)


def test_load_tsv_ignores_extra_columns(tmp_path: Path) -> None:
    tsv = write_tsv(
        tmp_path / "meta.tsv",
        "legal_owner\tproject\tnotes\nAlice\tproj_a\tExample\n",
    )
    assert load_project_metadata(tsv) == {PN("proj_a"): ProjectMetadata(OW("Alice"))}


def test_load_tsv_empty_file(tmp_path: Path) -> None:
    tsv = write_tsv(tmp_path / "meta.tsv", "")
    with pytest.raises(ValueError, match="empty or has no header"):
        load_project_metadata(tsv)


def test_resolve_all_present() -> None:
    tsv_metadata = {
        PN("proj_a"): ProjectMetadata(OW("Alice")),
        PN("proj_b"): ProjectMetadata(OW("Bob")),
    }

    result = resolve_project_metadata([PN("proj_a"), PN("proj_b")], tsv_metadata)

    assert result == tsv_metadata


def test_resolve_missing_project_warns(caplog: pytest.LogCaptureFixture) -> None:
    tsv_metadata = {PN("proj_a"): ProjectMetadata(OW("Alice"))}

    with caplog.at_level(logging.WARNING, logger="durep"):
        result = resolve_project_metadata([PN("proj_a"), PN("proj_b")], tsv_metadata)

    assert result == {
        PN("proj_a"): ProjectMetadata(OW("Alice")),
        PN("proj_b"): ProjectMetadata(None),
    }
    assert "proj_b" in caplog.text
    assert "no metadata" in caplog.text


def test_resolve_extra_tsv_entries_ignored() -> None:
    tsv_metadata = {
        PN("proj_a"): ProjectMetadata(OW("Alice")),
        PN("proj_c"): ProjectMetadata(OW("Charlie")),
    }

    result = resolve_project_metadata([PN("proj_a")], tsv_metadata)

    assert result == {PN("proj_a"): ProjectMetadata(OW("Alice"))}
