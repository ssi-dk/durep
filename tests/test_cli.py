from __future__ import annotations

import json
from pathlib import Path

import pytest

from durep.cli import effective_overview_jobs, load_overview_samples, run


MINIMAL_NCDU = [
    1,
    2,
    {"progname": "ncdu", "progver": "2.7", "timestamp": 1700000000},
    [{"name": "/data", "asize": 100, "dsize": 100}],
]


def write_ncdu(
    path: Path,
    timestamp: int = 1700000000,
    root_name: str = "/data",
    dsize: int = 100,
) -> Path:
    data = [
        1,
        2,
        {"progname": "ncdu", "progver": "2.7", "timestamp": timestamp},
        [{"name": root_name, "asize": dsize, "dsize": dsize}],
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_metadata_csv(tmp_path: Path, projects: list[str]) -> Path:
    """Write a metadata CSV with each project as its own owner."""
    csv_path = tmp_path / "metadata.csv"
    lines = ["DisplayName,LegalOwner"]
    for p in projects:
        lines.append(f"{p},{p}")
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path


def normalize_generated(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("Generated:"))


# --- detail subcommand ---


def test_detail_single_scan_returns_zero(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    out_dir = tmp_path / "out"

    exit_code = run(["detail", str(scan), "--out-dir", str(out_dir)])

    assert exit_code == 0


def test_detail_creates_output_files(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    out_dir = tmp_path / "out"

    run(["detail", str(scan), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_detail_with_two_scans_creates_output_files(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "older.json", timestamp=1700000000)
    scan_b = write_ncdu(tmp_path / "newer.json", timestamp=1710000000)
    out_dir = tmp_path / "out"

    run(["detail", str(scan_a), str(scan_b), "--out-dir", str(out_dir)])

    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overall.html").is_file()


def test_detail_with_two_scans_order_independent(tmp_path: Path) -> None:
    scan_old = write_ncdu(tmp_path / "older.json", timestamp=1700000000)
    scan_new = write_ncdu(tmp_path / "newer.json", timestamp=1710000000)

    out_a = tmp_path / "out_a"
    run(["detail", str(scan_old), str(scan_new), "--out-dir", str(out_a)])
    report_a = (out_a / "overall.html").read_text(encoding="utf-8")

    out_b = tmp_path / "out_b"
    run(["detail", str(scan_new), str(scan_old), "--out-dir", str(out_b)])
    report_b = (out_b / "overall.html").read_text(encoding="utf-8")

    assert report_a == report_b


def test_detail_rejects_mismatched_roots(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", root_name="/data")
    scan_b = write_ncdu(tmp_path / "b.json", root_name="/other")

    with pytest.raises(ValueError, match="root directories do not match"):
        run(["detail", str(scan_a), str(scan_b), "--out-dir", str(tmp_path / "out")])


def test_detail_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run(["detail", str(tmp_path / "missing.json"), "--out-dir", str(tmp_path / "out")])


def test_detail_rejects_three_scans(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json")
    scan_b = write_ncdu(tmp_path / "b.json")
    scan_c = write_ncdu(tmp_path / "c.json")

    with pytest.raises(ValueError, match="at most two"):
        run(
            [
                "detail",
                str(scan_a),
                str(scan_b),
                str(scan_c),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )


# --- subcommand required ---


def test_run_requires_subcommand(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run([])


# --- overview subcommand ---


def test_overview_single_scan(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    meta = write_metadata_csv(tmp_path, ["data"])
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "overview",
            str(scan),
            "--out-dir",
            str(out_dir),
            "--metadata-csv-path",
            str(meta),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overview.html").is_file()


def test_overview_multiple_scans(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", timestamp=1700000000, root_name="/proj_a", dsize=500)
    scan_b = write_ncdu(tmp_path / "b.json", timestamp=1700086400, root_name="/proj_b", dsize=300)
    scan_c = write_ncdu(tmp_path / "c.json", timestamp=1700172800, root_name="/proj_a", dsize=600)
    meta = write_metadata_csv(tmp_path, ["proj_a", "proj_b"])
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "overview",
            str(scan_a),
            str(scan_b),
            str(scan_c),
            "--out-dir",
            str(out_dir),
            "--metadata-csv-path",
            str(meta),
        ]
    )

    assert exit_code == 0
    text = (out_dir / "text_report.txt").read_text(encoding="utf-8")
    assert "proj_a" in text
    assert "proj_b" in text


def test_overview_multiple_scans_with_jobs_2(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", timestamp=1700000000, root_name="/proj_a", dsize=500)
    scan_b = write_ncdu(tmp_path / "b.json", timestamp=1700086400, root_name="/proj_b", dsize=300)
    scan_c = write_ncdu(tmp_path / "c.json", timestamp=1700172800, root_name="/proj_a", dsize=600)
    meta = write_metadata_csv(tmp_path, ["proj_a", "proj_b"])
    out_dir = tmp_path / "out"

    exit_code = run(
        [
            "overview",
            str(scan_a),
            str(scan_b),
            str(scan_c),
            "--jobs",
            "2",
            "--out-dir",
            str(out_dir),
            "--metadata-csv-path",
            str(meta),
        ]
    )

    assert exit_code == 0
    assert (out_dir / "text_report.txt").is_file()
    assert (out_dir / "overview.html").is_file()


def test_overview_jobs_1_matches_jobs_2_output(tmp_path: Path) -> None:
    scan_a = write_ncdu(tmp_path / "a.json", timestamp=1700000000, root_name="/proj_a", dsize=500)
    scan_b = write_ncdu(tmp_path / "b.json", timestamp=1700086400, root_name="/proj_b", dsize=300)
    scan_c = write_ncdu(tmp_path / "c.json", timestamp=1700172800, root_name="/proj_a", dsize=600)
    meta = write_metadata_csv(tmp_path, ["proj_a", "proj_b"])

    out_serial = tmp_path / "out_serial"
    run(
        [
            "overview",
            str(scan_a),
            str(scan_b),
            str(scan_c),
            "--jobs",
            "1",
            "--out-dir",
            str(out_serial),
            "--metadata-csv-path",
            str(meta),
        ]
    )

    out_parallel = tmp_path / "out_parallel"
    run(
        [
            "overview",
            str(scan_a),
            str(scan_b),
            str(scan_c),
            "--jobs",
            "2",
            "--out-dir",
            str(out_parallel),
            "--metadata-csv-path",
            str(meta),
        ]
    )

    serial_text = (out_serial / "text_report.txt").read_text(encoding="utf-8")
    parallel_text = (out_parallel / "text_report.txt").read_text(encoding="utf-8")
    assert normalize_generated(serial_text) == normalize_generated(parallel_text)

    serial_html = (out_serial / "overview.html").read_text(encoding="utf-8")
    parallel_html = (out_parallel / "overview.html").read_text(encoding="utf-8")
    assert normalize_generated(serial_html) == normalize_generated(parallel_html)


def test_overview_rejects_missing_file(tmp_path: Path) -> None:
    meta = write_metadata_csv(tmp_path, [])
    with pytest.raises(FileNotFoundError):
        run(
            [
                "overview",
                str(tmp_path / "missing.json"),
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(meta),
            ]
        )


def test_overview_rejects_jobs_zero(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")

    with pytest.raises(SystemExit):
        run(
            [
                "overview",
                str(scan),
                "--jobs",
                "0",
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(tmp_path / "meta.csv"),
            ]
        )


def test_overview_rejects_negative_jobs(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")

    with pytest.raises(SystemExit):
        run(
            [
                "overview",
                str(scan),
                "--jobs",
                "-1",
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(tmp_path / "meta.csv"),
            ]
        )


def test_overview_rejects_missing_timestamp(tmp_path: Path) -> None:
    no_ts = [
        1,
        2,
        {"progname": "ncdu", "progver": "2.7"},
        [{"name": "/data", "asize": 100, "dsize": 100}],
    ]
    scan = tmp_path / "no_ts.json"
    scan.write_text(json.dumps(no_ts), encoding="utf-8")
    meta = write_metadata_csv(tmp_path, ["data"])

    with pytest.raises(ValueError, match="timestamp"):
        run(
            [
                "overview",
                str(scan),
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(meta),
            ]
        )


def test_overview_parallel_preserves_parse_error_details(tmp_path: Path) -> None:
    good = write_ncdu(tmp_path / "good.json")
    bad = tmp_path / "bad.json"
    bad.write_text(
        '[1, 2, {"progname": "ncdu", "timestamp": 1700000000}, {"name": }]', encoding="utf-8"
    )
    meta = write_metadata_csv(tmp_path, ["data"])

    with pytest.raises(ValueError, match=r"Invalid NCDU JSON file at .*parse error:"):
        run(
            [
                "overview",
                str(good),
                str(bad),
                "--jobs",
                "2",
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(meta),
            ]
        )


def test_overview_rejects_missing_metadata_csv(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    with pytest.raises(FileNotFoundError):
        run(
            [
                "overview",
                str(scan),
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(tmp_path / "nonexistent.csv"),
            ]
        )


def test_overview_rejects_bad_metadata_csv_columns(tmp_path: Path) -> None:
    scan = write_ncdu(tmp_path / "scan.json")
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Name,Owner\nproj,Alice\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required column"):
        run(
            [
                "overview",
                str(scan),
                "--out-dir",
                str(tmp_path / "out"),
                "--metadata-csv-path",
                str(bad_csv),
            ]
        )


def test_effective_overview_jobs_caps_auto_workers_at_8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("durep.cli.os.cpu_count", lambda: 64)

    assert effective_overview_jobs(None, 20) == 8


def test_effective_overview_jobs_is_bounded_by_scan_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("durep.cli.os.cpu_count", lambda: 64)

    assert effective_overview_jobs(None, 3) == 3


def test_load_overview_samples_falls_back_to_serial_when_process_pool_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scan_a = tmp_path / "a.json"
    scan_b = tmp_path / "b.json"
    scans = [scan_a, scan_b]

    class RaisingExecutor:
        def __init__(self, max_workers: int) -> None:
            raise PermissionError("blocked")

    monkeypatch.setattr("durep.cli.ProcessPoolExecutor", RaisingExecutor)
    monkeypatch.setattr(
        "durep.cli.load_overview_sample",
        lambda scan_path: scan_path.name,  # type: ignore[return-value]
    )

    assert load_overview_samples(scans, jobs=2) == ["a.json", "b.json"]
