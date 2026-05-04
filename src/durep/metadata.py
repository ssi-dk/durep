from __future__ import annotations

import logging
from collections.abc import Sequence
from csv import DictReader
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

log = logging.getLogger("durep")

DISPLAY_NAME_COLUMN = "project"
LEGAL_OWNER_COLUMN = "legal_owner"
PROJECT_LEAD_COLUMN = "project_lead"

ProjectName = NewType("ProjectName", str)
Owner = NewType("Owner", str)
ProjectLead = NewType("ProjectLead", str)


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    legal_owner: Owner | None
    project_leads: tuple[ProjectLead, ...] = ()


def split_project_leads(raw: str) -> tuple[ProjectLead, ...]:
    return tuple(ProjectLead(part.strip()) for part in raw.split(",") if part.strip())


def load_project_metadata(tsv_path: Path) -> dict[ProjectName, ProjectMetadata]:
    """Parse a metadata TSV and return metadata by project display name.

    The TSV must contain project, legal_owner, and project_lead columns. A
    blank legal_owner means the project has no legal owner. A blank
    project_lead means no project leads.
    """
    with tsv_path.open(newline="", encoding="utf-8") as f:
        reader = DictReader(f, delimiter="\t")
        if reader.fieldnames is None:
            raise ValueError(
                f"metadata TSV {tsv_path} is empty or has no header row; "
                f"expected columns: {DISPLAY_NAME_COLUMN}, {LEGAL_OWNER_COLUMN}"
            )
        missing = {DISPLAY_NAME_COLUMN, LEGAL_OWNER_COLUMN, PROJECT_LEAD_COLUMN} - set(
            reader.fieldnames
        )
        if missing:
            raise ValueError(
                f"metadata TSV {tsv_path} is missing required column(s): {', '.join(sorted(missing))}"
            )

        result: dict[ProjectName, ProjectMetadata] = {}
        for row in reader:
            name = (row[DISPLAY_NAME_COLUMN] or "").strip()
            if name:
                raw_owner = (row[LEGAL_OWNER_COLUMN] or "").strip()
                raw_leads = (row[PROJECT_LEAD_COLUMN] or "").strip()
                result[ProjectName(name)] = ProjectMetadata(
                    legal_owner=Owner(raw_owner) if raw_owner else None,
                    project_leads=split_project_leads(raw_leads),
                )
        return result


def resolve_project_metadata(
    projects: Sequence[ProjectName],
    tsv_metadata: dict[ProjectName, ProjectMetadata],
) -> dict[ProjectName, ProjectMetadata]:
    """Return metadata for the given project names, warning for missing entries."""
    metadata: dict[ProjectName, ProjectMetadata] = {}
    for project in projects:
        raw_metadata = tsv_metadata.get(project)
        if raw_metadata is not None:
            metadata[project] = raw_metadata
        else:
            log.warning(
                "Project %r has no metadata in metadata TSV",
                project,
            )
            metadata[project] = ProjectMetadata(legal_owner=None, project_leads=())
    return metadata
