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


def write_ncdu(path: Path, timestamp: int = 1700000000, root_name: str = "/data") -> Path:
    data = [
        1,
        2,
        {"progname": "ncdu", "progver": "2.7", "timestamp": timestamp},
        [{"name": root_name, "asize": 100, "dsize": 100}],
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_run_single_scan_returns_zero(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    out_dir = tmp_path / "out"

    exit_code = run([str(scan), "--out-dir", str(out_dir)])

    assert exit_code == 0


def test_run_creates_output_files(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    out_dir = tmp_path / "out"

    run([str(scan), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_run_with_two_scans_creates_output_files(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "older.json", timestamp=1700000000)
    scan_b = write_ncdu(tmp_path / "newer.json", timestamp=1710000000)
    out_dir = tmp_path / "out"

    run([str(scan_a), str(scan_b), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_run_with_two_scans_order_independent(tmp_path: Path) -> None:
    scan_old = write_ncdu(tmp_path / "older.json", timestamp=1700000000)
    scan_new = write_ncdu(tmp_path / "newer.json", timestamp=1710000000)

    out_a = tmp_path / "out_a"
    run([str(scan_old), str(scan_new), "--out-dir", str(out_a)])
    report_a = (out_a / "overall.html").read_text(encoding="utf-8")

    out_b = tmp_path / "out_b"
    run([str(scan_new), str(scan_old), "--out-dir", str(out_b)])
    report_b = (out_b / "overall.html").read_text(encoding="utf-8")

    assert report_a == report_b


def test_run_rejects_mismatched_roots(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", root_name="/data")
    scan_b = write_ncdu(tmp_path / "b.json", root_name="/other")

    with pytest.raises(ValueError, match="root directories do not match"):
        run([str(scan_a), str(scan_b), "--out-dir", str(tmp_path / "out")])


def test_run_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run([str(tmp_path / "missing.json"), "--out-dir", str(tmp_path / "out")])


def test_run_rejects_three_scans(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json")
    scan_b = write_ncdu(tmp_path / "b.json")
    scan_c = write_ncdu(tmp_path / "c.json")

    with pytest.raises(ValueError, match="at most two"):
        run([str(scan_a), str(scan_b), str(scan_c), "--out-dir", str(tmp_path / "out")])


def test_run_rejects_missing_timestamp_with_two_scans(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", timestamp=1700000000)
    # Write a scan without timestamp
    no_ts = [1, 2, [{"name": "/data", "asize": 100, "dsize": 100}]]
    scan_b = tmp_path / "b.json"
    scan_b.write_text(json.dumps(no_ts), encoding="utf-8")

    with pytest.raises(ValueError, match="missing timestamp"):
        run([str(scan_a), str(scan_b), "--out-dir", str(tmp_path / "out")])
