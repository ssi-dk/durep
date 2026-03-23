from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from durep.analytics import (
    build_drilldown_tree,
    build_growth_drilldown,
    build_overview_series,
    compute_directory_deltas,
    compute_global_metrics,
    extract_project_sample,
)
from durep.ncdu import NcduRun, parse_ncdu_json_file, path_str
from durep.reports import (
    render_html_report,
    render_overview_html_report,
    render_overview_text_report,
    render_text_report,
)

log = logging.getLogger("durep")


@dataclass(slots=True)
class DetailArgs:
    scans: list[Path]
    out_dir: Path
    top_n: int
    max_depth: int

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> DetailArgs:
        return cls(
            scans=[Path(p) for p in namespace.scan],
            out_dir=Path(namespace.out_dir),
            top_n=namespace.top_n,
            max_depth=namespace.max_depth,
        )

    def validate(self) -> None:
        if len(self.scans) > 2:
            raise ValueError("at most two scan files may be provided")
        for scan in self.scans:
            require_file(scan, str(scan))


@dataclass(slots=True)
class OverviewArgs:
    scans: list[Path]
    out_dir: Path

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> OverviewArgs:
        return cls(
            scans=[Path(p) for p in namespace.scan],
            out_dir=Path(namespace.out_dir),
        )

    def validate(self) -> None:
        for scan in self.scans:
            require_file(scan, str(scan))


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate disk usage reports from ncdu JSON scans."
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (e.g. DEBUG, INFO, WARNING, ERROR, CRITICAL, or an integer)."
        " Falls back to DUREP_LOG_LEVEL env var, then WARNING.",
    )

    subparsers = parser.add_subparsers(dest="subcommand")

    # detail subcommand
    detail = subparsers.add_parser(
        "detail",
        help="Detailed per-project report with sunburst diagrams.",
    )
    detail.add_argument(
        "scan",
        nargs="+",
        help="One or two ncdu JSON scan paths."
        " When two are given, timestamps determine which is current vs previous.",
    )
    detail.add_argument("--out-dir", required=True, help="Output directory for generated reports.")
    detail.add_argument(
        "--top-n",
        type=positive_int,
        default=25,
        help="Maximum number of children to keep per expanded node (default: 25).",
    )
    detail.add_argument(
        "--max-depth",
        type=positive_int,
        default=100,
        help="Maximum expansion depth used for drilldown datasets (default: 100).",
    )

    # overview subcommand
    overview = subparsers.add_parser(
        "overview",
        help="High-level overview comparing many projects over time.",
    )
    overview.add_argument(
        "scan",
        nargs="+",
        help="One or more ncdu JSON scan paths.",
    )
    overview.add_argument(
        "--out-dir", required=True, help="Output directory for generated reports."
    )

    return parser


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    return path


def parse_log_level(raw: str) -> int:
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        pass
    name_map = logging.getLevelNamesMapping()
    upper = raw.upper()
    if upper in name_map:
        return name_map[upper]
    raise ValueError(f"invalid log level: {raw!r}")


def configure_logging(cli_value: str | None) -> None:
    raw = cli_value or os.environ.get("DUREP_LOG_LEVEL") or "WARNING"
    try:
        level = parse_log_level(raw)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from None
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def order_runs(run_a: NcduRun, run_b: NcduRun) -> tuple[NcduRun, NcduRun]:
    """Return (current_run, previous_run) based on timestamps.

    Raises ValueError if either run is missing a timestamp.
    """
    if run_a.timestamp is None or run_b.timestamp is None:
        missing = []
        if run_a.timestamp is None:
            missing.append(str(path_str(run_a.root)))
        if run_b.timestamp is None:
            missing.append(str(path_str(run_b.root)))
        raise ValueError(
            "cannot determine scan order: missing timestamp in "
            + ", ".join(missing)
            + ". Both scans must contain a timestamp when comparing two files."
        )
    if run_a.timestamp >= run_b.timestamp:
        return run_a, run_b
    return run_b, run_a


def execute_detail(args: DetailArgs) -> None:
    out_dir = args.out_dir
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    log.info("Output directory: %s", out_dir)

    log.info("Parsing scan: %s", args.scans[0])
    run_a = parse_ncdu_json_file(args.scans[0], top_n=args.top_n)
    log.debug(
        "Scan: %d files, %d directories",
        run_a.root.total_files,
        run_a.root.total_directories,
    )

    previous_run: NcduRun | None = None
    if len(args.scans) == 2:
        log.info("Parsing scan: %s", args.scans[1])
        run_b = parse_ncdu_json_file(args.scans[1], top_n=args.top_n)
        log.debug(
            "Scan: %d files, %d directories",
            run_b.root.total_files,
            run_b.root.total_directories,
        )

        if path_str(run_a.root) != path_str(run_b.root):
            raise ValueError(
                f"root directories do not match: {path_str(run_a.root)} vs {path_str(run_b.root)}."
                " Both scans must be of the same directory."
            )

        current_run, previous_run = order_runs(run_a, run_b)
    else:
        current_run = run_a

    metrics = compute_global_metrics(current_run.root)

    deltas = None
    if previous_run is not None:
        log.debug("Computing deltas")
        deltas = compute_directory_deltas(current_run.root, previous_run.root)
        log.info("Computed deltas for %d paths", len(deltas))

    text = render_text_report(current_run, previous_run, metrics, deltas, args.top_n)
    text_path = out_dir / "text_report.txt"
    text_path.write_text(text, encoding="utf-8")
    log.info("Wrote text report: %s", text_path)

    log.debug("Building drilldown tree (top_n=%d, max_depth=%d)", args.top_n, args.max_depth)
    drilldown = build_drilldown_tree(current_run.root, args.top_n, args.max_depth, deltas)

    growth_drilldown = None
    if deltas is not None:
        log.debug("Building growth drilldown")
        growth_drilldown = build_growth_drilldown(
            current_run.root, deltas, args.top_n, args.max_depth
        )

    html = render_html_report(current_run, previous_run, drilldown, metrics, growth_drilldown, text)
    html_path = out_dir / "overall.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("Wrote HTML report: %s", html_path)


def execute_overview(args: OverviewArgs) -> None:
    out_dir = args.out_dir
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    log.info("Output directory: %s", out_dir)

    samples = []
    for scan_path in args.scans:
        log.info("Parsing scan: %s", scan_path)
        run = parse_ncdu_json_file(scan_path)
        sample = extract_project_sample(run)
        samples.append(sample)

    series = build_overview_series(samples)

    text = render_overview_text_report(series, samples)
    text_path = out_dir / "text_report.txt"
    text_path.write_text(text, encoding="utf-8")
    log.info("Wrote text report: %s", text_path)

    html = render_overview_html_report(series, text)
    html_path = out_dir / "overview.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("Wrote HTML report: %s", html_path)


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    configure_logging(namespace.log_level)
    if namespace.subcommand is None:
        parser.error("a subcommand is required (detail, overview)")
    if namespace.subcommand == "detail":
        args = DetailArgs.from_namespace(namespace)
        args.validate()
        execute_detail(args)
    elif namespace.subcommand == "overview":
        args = OverviewArgs.from_namespace(namespace)
        args.validate()
        execute_overview(args)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
        configure_logging(namespace.log_level)
        if namespace.subcommand is None:
            parser.error("a subcommand is required (detail, overview)")
        if namespace.subcommand == "detail":
            args = DetailArgs.from_namespace(namespace)
            args.validate()
            execute_detail(args)
        elif namespace.subcommand == "overview":
            args = OverviewArgs.from_namespace(namespace)
            args.validate()
            execute_overview(args)
        raise SystemExit(0)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")


if __name__ == "__main__":
    main()
