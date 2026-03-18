from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import NewType


UNASSIGNED_PROJECT = "UNASSIGNED"

Project = NewType("Project", str)


@dataclass(slots=True)
class MappingRule:
    directory: Path
    project: Project


def parse_mapping_csv_file(path: Path) -> list[MappingRule]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("Mapping CSV is empty") from exc

        if header != ["directory", "project"]:
            raise ValueError("Mapping CSV header must be exactly: directory,project")

        rules: list[MappingRule] = []
        for line_number, row in enumerate(reader, start=2):
            if len(row) != 2:
                raise ValueError(
                    f"Mapping CSV line {line_number} must have exactly 2 columns: directory,project"
                )

            directory_raw, project_raw = row
            directory = Path(directory_raw.strip())
            project = Project(project_raw.strip())

            if not directory.is_absolute():
                raise ValueError(
                    f"Mapping CSV line {line_number} directory must be absolute: {directory}"
                )
            if project == "":
                raise ValueError(f"Mapping CSV line {line_number} project must be non-empty")

            rules.append(MappingRule(directory=directory, project=project))

    return rules


def assign_project(path: Path, rules: list[MappingRule]) -> str:
    best_project = UNASSIGNED_PROJECT
    best_prefix_length = -1

    for rule in rules:
        if path_is_within_prefix(path=path, prefix=rule.directory):
            prefix_length = len(rule.directory.parts)
            if prefix_length > best_prefix_length:
                best_project = rule.project
                best_prefix_length = prefix_length

    return best_project


def path_is_within_prefix(path: Path, prefix: Path) -> bool:
    try:
        path.resolve().relative_to(prefix.resolve())
        return True
    except ValueError:
        return False
