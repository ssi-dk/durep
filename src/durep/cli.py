from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from durep.analytics import (
    compute_all_uncompressed_stats,
    compute_directory_deltas,
    compute_global_metrics,
)
from durep.ncdu import parse_ncdu_json_file
from durep.reports import render_text_report


@dataclass(slots=True)
class CliArgs:
    current: Path
    previous: Path | None
    out_dir: Path
    top_n: int
    max_depth: int

    @classmethod
    def parse_cli_args(cls, argv: Sequence[str] | None = None) -> CliArgs:
        parser = build_parser()
        namespace = parser.parse_args(argv)
        return cls(
            current=Path(namespace.current),
            previous=Path(namespace.previous) if namespace.previous else None,
            out_dir=Path(namespace.out_dir),
            top_n=namespace.top_n,
            max_depth=namespace.max_depth,
        )

    @classmethod
    def validated_from_cli_args(cls, argv: Sequence[str] | None = None) -> CliArgs:
        instance = cls.parse_cli_args(argv)
        instance.validate()
        return instance

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
        description="Generate disk usage reports from ncdu JSON snapshots."
    )
    parser.add_argument("--current", required=True, help="Current ncdu JSON snapshot path.")
    parser.add_argument(
        "--previous",
        required=False,
        help="Previous ncdu JSON snapshot path used for diff calculations.",
    )
    parser.add_argument("--out-dir", required=True, help="Output directory for generated reports.")
    parser.add_argument(
        "--top-n",
        type=positive_int,
        default=15,
        help="Maximum number of children to keep per expanded node (default: 15).",
    )
    parser.add_argument(
        "--max-depth",
        type=positive_int,
        default=4,
        help="Maximum expansion depth used for drilldown datasets (default: 4).",
    )
    return parser


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: {path}")
    return path


def parse_and_validate_args(argv: Sequence[str] | None = None) -> CliArgs:
    args = CliArgs.parse_cli_args(argv)
    args.validate()
    return args


def execute(args: CliArgs) -> None:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    current_root = parse_ncdu_json_file(args.current)
    previous_root = None
    if args.previous is not None:
        previous_root = parse_ncdu_json_file(args.previous)

    uncompressed = compute_all_uncompressed_stats(current_root)
    metrics = compute_global_metrics(current_root, uncompressed)

    deltas = None
    if previous_root is not None:
        deltas = compute_directory_deltas(current_root, previous_root)

    text = render_text_report(current_root, metrics, deltas, args.top_n)
    (out_dir / "text_report.txt").write_text(text, encoding="utf-8")

    # TODO: render HTML report and write overall.html


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_and_validate_args(argv)
    execute(args)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    try:
        args = parse_and_validate_args(argv)
        execute(args)
        raise SystemExit(0)
    except FileNotFoundError as exc:
        parser.exit(status=1, message=f"error: {exc}\n")


if __name__ == "__main__":
    main()
