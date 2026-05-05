from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from durep import __version__
from durep.workflows import (
    effective_jobs,
    load_detail_runs,
    load_overview_samples,
    write_detail_reports,
    write_overview_reports,
)

log = logging.getLogger("durep")


@dataclass(slots=True)
class DetailArgs:
    scans: list[Path]
    out_dir: Path
    top_n: int
    display_nodes: int

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> DetailArgs:
        return cls(
            scans=[Path(p) for p in namespace.scan],
            out_dir=Path(namespace.out_dir),
            top_n=namespace.top_n,
            display_nodes=namespace.display_nodes,
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
    jobs: int | None
    metadata_tsv: Path | None

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> OverviewArgs:
        raw_tsv = namespace.metadata_tsv_path
        return cls(
            scans=[Path(p) for p in namespace.scan],
            out_dir=Path(namespace.out_dir),
            jobs=namespace.jobs,
            metadata_tsv=Path(raw_tsv) if raw_tsv is not None else None,
        )

    def validate(self) -> None:
        if self.jobs is not None and self.jobs < 1:
            raise ValueError("jobs must be an integer > 0")
        if self.metadata_tsv is not None:
            require_file(self.metadata_tsv, "--metadata-tsv-path")
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
        "-V",
        "--version",
        action="version",
        version=f"durep {__version__}",
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
        default=20,
        help="Maximum number of children to keep per expanded node (default: 20).",
    )
    detail.add_argument(
        "--display-nodes",
        type=positive_int,
        default=8000,
        help="Maximum number of nodes in the display tree (default: 8000)."
        " Directories are expanded largest-first until this budget is reached.",
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
        "--jobs",
        type=positive_int,
        default=None,
        help="Worker processes for parsing overview scans. If omitted, auto-select and cap at 8.",
    )
    overview.add_argument(
        "--out-dir", required=True, help="Output directory for generated reports."
    )
    overview.add_argument(
        "--metadata-tsv-path",
        default=None,
        help="TSV file with project, legal_owner, and project_lead columns."
        " If omitted, projects are listed individually without metadata filters.",
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


def execute_detail(args: DetailArgs) -> None:
    current_run, previous_run = load_detail_runs(args.scans, args.top_n, args.display_nodes)
    write_detail_reports(args.out_dir, current_run, previous_run, args.top_n)


def execute_overview(args: OverviewArgs) -> None:
    jobs = effective_jobs(args.jobs, len(args.scans))
    samples = load_overview_samples(args.scans, jobs)
    write_overview_reports(args.out_dir, samples, args.metadata_tsv)


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    configure_logging(namespace.log_level)
    if namespace.subcommand is None:
        parser.error("a subcommand is required (detail, overview)")
    if namespace.subcommand == "detail":
        detail_args = DetailArgs.from_namespace(namespace)
        detail_args.validate()
        execute_detail(detail_args)
    elif namespace.subcommand == "overview":
        overview_args = OverviewArgs.from_namespace(namespace)
        overview_args.validate()
        execute_overview(overview_args)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    try:
        run(argv)
        raise SystemExit(0)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from None


if __name__ == "__main__":
    main()
