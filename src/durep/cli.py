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
    compute_all_uncompressed_stats,
    compute_directory_deltas,
    compute_global_metrics,
)
from durep.ncdu import NcduRun, parse_ncdu_json_file
from durep.reports import render_html_report, render_text_report

log = logging.getLogger("durep")


@dataclass(slots=True)
class CliArgs:
    current: Path
    previous: Path | None
    out_dir: Path
    top_n: int
    max_depth: int

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> CliArgs:
        return cls(
            current=Path(namespace.current),
            previous=Path(namespace.previous) if namespace.previous else None,
            out_dir=Path(namespace.out_dir),
            top_n=namespace.top_n,
            max_depth=namespace.max_depth,
        )

    def validate(self) -> None:
        require_file(self.current, "current")
        if self.previous is not None:
            require_file(self.previous, "previous")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate disk usage reports from ncdu JSON scans."
    )
    parser.add_argument("--current", required=True, help="Current ncdu JSON scan path.")
    parser.add_argument(
        "--previous",
        required=False,
        help="Previous ncdu JSON scan path used for diff calculations.",
    )
    parser.add_argument("--out-dir", required=True, help="Output directory for generated reports.")
    parser.add_argument(
        "--top-n",
        type=positive_int,
        default=25,
        help="Maximum number of children to keep per expanded node (default: 25).",
    )
    parser.add_argument(
        "--max-depth",
        type=positive_int,
        # TODO: Tweak based on empirical data from production NCDU files.
        # In practice, directory trees are sparse so deep expansion is fine.
        # Monitor HTML file size and browser responsiveness on large scans.
        default=100,
        help="Maximum expansion depth used for drilldown datasets (default: 100).",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Log level (e.g. DEBUG, INFO, WARNING, ERROR, CRITICAL, or an integer)."
        " Falls back to DUREP_LOG_LEVEL env var, then WARNING.",
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


def parse_and_validate_args(namespace: argparse.Namespace) -> CliArgs:
    args = CliArgs.from_namespace(namespace)
    args.validate()
    return args


def execute(args: CliArgs) -> None:
    out_dir = args.out_dir
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    log.info("Output directory: %s", out_dir)

    log.info("Parsing current scan: %s", args.current)
    current_run = parse_ncdu_json_file(args.current)
    log.debug(
        "Current scan: %d files, %d directories",
        current_run.root.total_files,
        current_run.root.total_directories,
    )

    previous_run: NcduRun | None = None
    if args.previous is not None:
        log.info("Parsing previous scan: %s", args.previous)
        previous_run = parse_ncdu_json_file(args.previous)
        log.debug(
            "Previous scan: %d files, %d directories",
            previous_run.root.total_files,
            previous_run.root.total_directories,
        )

    log.debug("Computing uncompressed stats")
    uncompressed = compute_all_uncompressed_stats(current_run.root)
    metrics = compute_global_metrics(current_run.root, uncompressed)

    deltas = None
    if previous_run is not None:
        log.debug("Computing directory deltas")
        deltas = compute_directory_deltas(current_run.root, previous_run.root)
        log.info("Computed deltas for %d directories", len(deltas))

    text = render_text_report(current_run, previous_run, metrics, deltas, args.top_n)
    text_path = out_dir / "text_report.txt"
    text_path.write_text(text, encoding="utf-8")
    log.info("Wrote text report: %s", text_path)

    log.debug("Building drilldown tree (top_n=%d, max_depth=%d)", args.top_n, args.max_depth)
    drilldown = build_drilldown_tree(
        current_run.root, uncompressed, args.top_n, args.max_depth, deltas
    )

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


def run(argv: Sequence[str] | None = None) -> int:
    namespace = build_parser().parse_args(argv)
    configure_logging(namespace.log_level)
    args = parse_and_validate_args(namespace)
    execute(args)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    try:
        namespace = parser.parse_args(argv)
        configure_logging(namespace.log_level)
        args = parse_and_validate_args(namespace)
        execute(args)
        raise SystemExit(0)
    except (FileNotFoundError, FileExistsError) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")


if __name__ == "__main__":
    main()
