# Module to search through the JSON paths to find the relevant ones

from pathlib import Path
from durep.utils import Project
import re
from datetime import datetime

def project_paths(paths: list[Path]) -> list[tuple[Project, Path, Path | None]]:
    # Parse all project directories according to this regex, where the two regex
    # capture groups are the project, and the ISO 8601 datetime
    regex = re.compile("^_users_data_Projects_(.+)-([0-9]{8}T[0-9]{6}).json$")
    by_project: dict[Project, list[tuple[datetime, Path]]] = dict()
    for path in paths:
        match = regex.match(str(path))
        if match is None:
            continue
        
        project = Project(match.group(1))
        stamp = datetime.strptime(match.group(2), "%Y%m%dT%H%M%S")
        by_project.setdefault(project, []).append((stamp, path))

    result: list[tuple[Project, Path, Path | None]] = []
    for (project, entries) in by_project.items():
        entries.sort(key=lambda x: x[0])
        if len(entries) < 2:
            (last, previous) = (entries[-1][1], None)
        else:
            (last, previous) = (entries[-1][1], entries[-2][1])
        result.append((project, last, previous))

    return result