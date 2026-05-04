from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from durep.analytics import (
    ProjectSample,
    build_drilldown_tree,
    build_overview_series,
    compute_directory_deltas,
    compute_global_metrics,
)
from durep.metadata import (
    ProjectMetadata,
    ProjectName,
    load_project_metadata,
    resolve_project_metadata,
)
from durep.ncdu import NcduRun, parse_ncdu_json_file, parse_ncdu_project_sample, path_str
from durep.reports import (
    render_html_report,
    render_overview_html_report,
    render_overview_text_report,
    render_text_report,
)

log = logging.getLogger("durep")


def effective_jobs(requested_jobs: int | None, job_count: int) -> int:
    if requested_jobs is not None:
        if requested_jobs < 1:
            raise ValueError("jobs must be an integer > 0")
        return requested_jobs
    return min(job_count, os.cpu_count() or 1, 8)


def estimate_input_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        log.warning("Could not stat input for scheduling: %s", path)
        return 0


def load_overview_sample(scan_path: Path) -> ProjectSample:
    return parse_ncdu_project_sample(scan_path)


def schedule_overview_scans(scans: list[Path]) -> list[Path]:
    """Schedule larger overview scans first to reduce worker tail latency."""
    return sorted(scans, key=estimate_input_size, reverse=True)


def load_overview_samples(scans: list[Path], jobs: int) -> list[ProjectSample]:
    scheduled_scans = schedule_overview_scans(scans)

    for scan_path in scheduled_scans:
        log.info("Queueing overview scan: %s", scan_path)

    if jobs <= 1:
        return [load_overview_sample(scan_path) for scan_path in scheduled_scans]

    log.info("Loading overview samples with %d worker processes", jobs)
    try:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            try:
                return list(executor.map(load_overview_sample, scheduled_scans))
            except ValueError as exc:
                raise ValueError(str(exc)) from None
    except (NotImplementedError, PermissionError):
        log.warning("Process pool unavailable; falling back to serial overview parsing")
        return [load_overview_sample(scan_path) for scan_path in scheduled_scans]


def order_runs(run_a: NcduRun, run_b: NcduRun) -> tuple[NcduRun, NcduRun]:
    """Return (current_run, previous_run) based on timestamps."""
    if run_a.timestamp >= run_b.timestamp:
        return run_a, run_b
    return run_b, run_a


def load_detail_runs(
    scans: Sequence[Path], top_n: int, display_nodes: int
) -> tuple[NcduRun, NcduRun | None]:
    if not scans:
        raise ValueError("at least one scan file must be provided")
    if len(scans) > 2:
        raise ValueError("at most two scan files may be provided")

    run_a = parse_ncdu_json_file(scans[0], top_n=top_n, display_nodes=display_nodes)

    previous_run: NcduRun | None = None
    if len(scans) == 2:
        run_b = parse_ncdu_json_file(scans[1], top_n=top_n, display_nodes=display_nodes)

        if path_str(run_a.root) != path_str(run_b.root):
            raise ValueError(
                f"root directories do not match: {path_str(run_a.root)} vs {path_str(run_b.root)}."
                " Both scans must be of the same directory."
            )

        current_run, previous_run = order_runs(run_a, run_b)
    else:
        current_run = run_a

    return current_run, previous_run


def write_detail_reports(
    out_dir: Path,
    current_run: NcduRun,
    previous_run: NcduRun | None,
    top_n: int,
) -> None:
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")

    metrics = compute_global_metrics(current_run.root)

    deltas = None
    if previous_run is not None:
        log.debug("Computing deltas")
        deltas = compute_directory_deltas(current_run.root, previous_run.root)
        log.info("Computed deltas for %d paths", len(deltas))

    text = render_text_report(current_run, previous_run, metrics, deltas, top_n)

    log.debug("Building drilldown tree (top_n=%d)", top_n)
    drilldown = build_drilldown_tree(current_run.root, top_n, deltas)

    html = render_html_report(current_run, previous_run, drilldown, metrics, text)

    out_dir.mkdir(parents=True)
    log.info("Output directory: %s", out_dir)

    text_path = out_dir / "report.txt"
    text_path.write_text(text, encoding="utf-8")
    log.info("Wrote text report: %s", text_path)

    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("Wrote HTML report: %s", html_path)


def write_overview_reports(
    out_dir: Path,
    samples: list[ProjectSample],
    metadata_tsv: Path | None,
) -> None:
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")

    series = build_overview_series(samples)

    metadata: dict[ProjectName, ProjectMetadata] | None = None
    if metadata_tsv is not None:
        tsv_metadata = load_project_metadata(metadata_tsv)
        metadata = resolve_project_metadata([ProjectName(s.project) for s in series], tsv_metadata)

    text = render_overview_text_report(series, samples, metadata)
    html = render_overview_html_report(series, text, samples, metadata)

    out_dir.mkdir(parents=True)
    log.info("Output directory: %s", out_dir)

    text_path = out_dir / "report.txt"
    text_path.write_text(text, encoding="utf-8")
    log.info("Wrote text report: %s", text_path)

    html_path = out_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("Wrote HTML report: %s", html_path)
