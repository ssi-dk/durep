from __future__ import annotations

import argparse
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(slots=True)
class CliArgs:
    current: Path
    previous: Path | None
    mapping: Path
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
            mapping=Path(namespace.mapping),
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
        require_file(self.mapping, "mapping")
        if self.previous is not None:
            require_file(self.previous, "previous")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate per-project disk usage reports from ncdu JSON snapshots."
    )
    parser.add_argument("--current", required=True, help="Current ncdu JSON snapshot path.")
    parser.add_argument(
        "--previous",
        required=False,
        help="Previous ncdu JSON snapshot path used for diff calculations.",
    )
    parser.add_argument(
        "--mapping", required=True, help="CSV file mapping directories to project and user."
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


def render_overall_html(current: Path, previous: Path | None, mapping: Path) -> str:
    previous_value = str(previous) if previous is not None else "not provided"
    title = "durep report (step 1 skeleton)"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{html.escape(title)}</title>\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{html.escape(title)}</h1>\n"
        "  <p>CLI scaffolding is in place. Data ingestion and analytics are implemented in later steps.</p>\n"
        "  <ul>\n"
        f"    <li>current: <code>{html.escape(str(current))}</code></li>\n"
        f"    <li>previous: <code>{html.escape(previous_value)}</code></li>\n"
        f"    <li>mapping: <code>{html.escape(str(mapping))}</code></li>\n"
        "  </ul>\n"
        "</body>\n"
        "</html>\n"
    )


def render_text_report(
    current: Path, previous: Path | None, mapping: Path, top_n: int, max_depth: int
) -> str:
    previous_value = str(previous) if previous is not None else "not provided"
    return "\n".join(
        [
            "durep report (step 1 skeleton)",
            "",
            f"current: {current}",
            f"previous: {previous_value}",
            f"mapping: {mapping}",
            f"top_n: {top_n}",
            f"max_depth: {max_depth}",
            "",
            "Outputs for this run:",
            "- overall.html",
            "- text_report.txt",
            "- projects/<project_slug>.html (generated in later implementation steps)",
            "",
        ]
    )


def parse_and_validate_args(argv: Sequence[str] | None = None) -> CliArgs:
    args = CliArgs.parse_cli_args(argv)
    args.validate()
    return args


def execute(args: CliArgs) -> None:
    current = args.current
    mapping = args.mapping
    previous = args.previous

    out_dir = args.out_dir
    projects_dir = out_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "overall.html").write_text(
        render_overall_html(current=current, previous=previous, mapping=mapping),
        encoding="utf-8",
    )
    (out_dir / "text_report.txt").write_text(
        render_text_report(
            current=current,
            previous=previous,
            mapping=mapping,
            top_n=args.top_n,
            max_depth=args.max_depth,
        ),
        encoding="utf-8",
    )


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
