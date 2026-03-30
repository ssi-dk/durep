from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import NewType

log = logging.getLogger("durep")

DISPLAY_NAME_COLUMN = "project"
LEGAL_OWNER_COLUMN = "group"

ProjectName = NewType("ProjectName", str)
Owner = NewType("Owner", str)


def load_project_owners(csv_path: Path) -> dict[ProjectName, Owner | None]:
    """Parse a metadata CSV and return a mapping of project display name to legal owner.

    Raises ValueError if the required columns (project, group) are missing.
    Returns None for projects whose group field is missing or blank.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(
                f"metadata CSV {csv_path} is empty or has no header row; "
                f"expected columns: {DISPLAY_NAME_COLUMN}, {LEGAL_OWNER_COLUMN}"
            )
        missing = {DISPLAY_NAME_COLUMN, LEGAL_OWNER_COLUMN} - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"metadata CSV {csv_path} is missing required column(s): {', '.join(sorted(missing))}"
            )
        result: dict[ProjectName, Owner | None] = {}
        for row in reader:
            name = (row[DISPLAY_NAME_COLUMN] or "").strip()
            raw_owner = (row[LEGAL_OWNER_COLUMN] or "").strip()
            if name:
                result[ProjectName(name)] = Owner(raw_owner) if raw_owner else None
        return result


def resolve_project_owners(
    projects: Sequence[ProjectName],
    csv_owners: dict[ProjectName, Owner | None],
) -> dict[ProjectName, Owner]:
    """Return a project-to-owner mapping for the given project names.

    Projects not in csv_owners, or whose owner is None, fall back to using
    the project name as the owner, with a warning emitted to stderr.
    """
    owners: dict[ProjectName, Owner] = {}
    for project in projects:
        raw_owner = csv_owners.get(project)
        if raw_owner is not None:
            owners[project] = raw_owner
        else:
            log.warning(
                "Project %r has no legal owner in metadata CSV; using project name as owner",
                project,
            )
            owners[project] = Owner(project)
    return owners
