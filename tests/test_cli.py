from __future__ import annotations

from pathlib import Path

import pytest

from durep.cli import run


def write_file(path: Path, content: str = "x") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_run_creates_step1_outputs(tmp_path: Path) -> None:
    current = write_file(tmp_path / "current.json", "{}")
    mapping = write_file(tmp_path / "mapping.csv", "directory,project,user\n")
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "--current",
            str(current),
            "--mapping",
            str(mapping),
            "--out-dir",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "overall.html").is_file()
    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "projects").is_dir()


def test_run_rejects_missing_required_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run(
            [
                "--current",
                str(tmp_path / "missing.json"),
                "--mapping",
                str(tmp_path / "missing.csv"),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )


def test_run_uses_default_numeric_args(tmp_path: Path) -> None:
    current = write_file(tmp_path / "current.json", "{}")
    mapping = write_file(tmp_path / "mapping.csv", "directory,project,user\n")
    out_dir = tmp_path / "out"

    run(
        [
            "--current",
            str(current),
            "--mapping",
            str(mapping),
            "--out-dir",
            str(out_dir),
        ]
    )

    text_report = (out_dir / "text_report.txt").read_text(encoding="utf-8")
    assert "top_n: 15" in text_report
    assert "max_depth: 4" in text_report
