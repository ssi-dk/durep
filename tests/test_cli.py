from __future__ import annotations

import json
from pathlib import Path

import pytest

from durep.cli import run


MINIMAL_NCDU = [
    1,
    2,
    {"progname": "ncdu", "progver": "2.7", "timestamp": 1700000000},
    [{"name": "/data", "asize": 100, "dsize": 100}],
]


def write_ncdu(path: Path) -> Path:
    path.write_text(json.dumps(MINIMAL_NCDU), encoding="utf-8")
    return path


def test_run_parses_and_returns_zero(tmp_path: Path) -> None:
    current = write_ncdu(tmp_path / "current.json")
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "--current",
            str(current),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0


def test_run_creates_output_files(tmp_path: Path) -> None:
    current = write_ncdu(tmp_path / "current.json")
    out_dir = tmp_path / "out"

    run(["--current", str(current), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_run_with_previous_creates_output_files(tmp_path: Path) -> None:
    current = write_ncdu(tmp_path / "current.json")
    previous = write_ncdu(tmp_path / "previous.json")
    out_dir = tmp_path / "out"

    run(["--current", str(current), "--previous", str(previous), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_run_rejects_missing_required_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run(
            [
                "--current",
                str(tmp_path / "missing.json"),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )
