from __future__ import annotations

from csv import DictReader
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

DISPLAY_NAME_COLUMN = "project"
LEGAL_OWNER_COLUMN = "legal_owner"

ProjectName = NewType("ProjectName", str)
Owner = NewType("Owner", str)


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    legal_owner: Owner | None


def load_project_metadata(tsv_path: Path) -> dict[ProjectName, ProjectMetadata]:
    """Parse a metadata TSV and return metadata by project display name.

    The TSV must contain project and legal_owner columns. A blank
    legal_owner means the project has no legal owner.
    """
    with tsv_path.open(newline="", encoding="utf-8") as f:
        reader = DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(
                f"metadata TSV {tsv_path} is empty or has no header row; "
                f"expected columns: {DISPLAY_NAME_COLUMN}, {LEGAL_OWNER_COLUMN}"
            )
        missing = {DISPLAY_NAME_COLUMN, LEGAL_OWNER_COLUMN} - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"metadata TSV {tsv_path} is missing required column(s): {', '.join(sorted(missing))}"
            )

        result: dict[ProjectName, ProjectMetadata] = {}
        for row in reader:
            name = (row[DISPLAY_NAME_COLUMN] or "").strip()
            row_is_blank = True
            for value in row.values():
                if isinstance(value, list):
                    if any(part.strip() for part in value):
                        row_is_blank = False
                        break
                elif value is not None and value.strip():
                    row_is_blank = False
                    break
            if row_is_blank:
                continue
            if not name:
                raise ValueError(
                    f"metadata TSV {tsv_path} contains a non-empty row without a project"
                )

            project = ProjectName(name)
            if project in result:
                raise ValueError(f"metadata TSV {tsv_path} contains duplicate project: {name!r}")

            raw_owner = (row[LEGAL_OWNER_COLUMN] or "").strip()
            result[project] = ProjectMetadata(
                legal_owner=Owner(raw_owner) if raw_owner else None,
            )
        return result
