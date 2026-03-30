from __future__ import annotations

import importlib.util
from pathlib import Path

from durep.analytics import ProjectSample
from durep.ncdu import UncompressedStats


def load_ugerm_module():
    script_path = Path(__file__).resolve().parents[2] / "deploy" / "dev" / "scripts" / "ugerm.py"
    spec = importlib.util.spec_from_file_location("ugerm_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_scan_histories_keeps_full_project_history(tmp_path: Path) -> None:
    ugerm = load_ugerm_module()

    paths = [
        tmp_path / "_users_data_Projects_projA-20260101T010101.json",
        tmp_path / "_users_data_Projects_projA-20260102T010101.json",
        tmp_path / "_users_data_Projects_projA-20260103T010101.json",
        tmp_path / "_users_home_user1-20260101T010101.json",
        tmp_path / "_users_home_user1-20260102T010101.json",
    ]
    for path in paths:
        path.write_text("{}", encoding="utf-8")

    projects, users = ugerm.get_scan_histories(paths)

    assert projects == [
        (
            "projA",
            [
                tmp_path / "_users_data_Projects_projA-20260101T010101.json",
                tmp_path / "_users_data_Projects_projA-20260102T010101.json",
                tmp_path / "_users_data_Projects_projA-20260103T010101.json",
            ],
        )
    ]
    assert users == [
        (
            "user1",
            [
                tmp_path / "_users_home_user1-20260101T010101.json",
                tmp_path / "_users_home_user1-20260102T010101.json",
            ],
        )
    ]


def test_build_detail_jobs_uses_only_latest_two_scans(tmp_path: Path) -> None:
    ugerm = load_ugerm_module()

    scans = [
        tmp_path / "_users_data_Projects_projA-20260101T010101.json",
        tmp_path / "_users_data_Projects_projA-20260102T010101.json",
        tmp_path / "_users_data_Projects_projA-20260103T010101.json",
    ]
    for path in scans:
        path.write_text("{}", encoding="utf-8")

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    jobs = ugerm.build_detail_jobs(
        projects=[("projA", scans)],
        users=[],
        output_dir=output_dir,
        top_n=20,
        display_nodes=5000,
    )

    assert len(jobs) == 1
    _, _, _, current_scan, previous_scan, _, _, collect_sample = jobs[0]
    assert current_scan == scans[-1]
    assert previous_scan == scans[-2]
    assert collect_sample is True


def test_load_historical_project_samples_only_uses_older_scans(monkeypatch, tmp_path: Path) -> None:
    ugerm = load_ugerm_module()

    old = tmp_path / "_users_data_Projects_projA-20260101T010101.json"
    prev = tmp_path / "_users_data_Projects_projA-20260102T010101.json"
    current = tmp_path / "_users_data_Projects_projA-20260103T010101.json"
    for path in (old, prev, current):
        path.write_text("{}", encoding="utf-8")

    seen: list[Path] = []

    def fake_load_overview_sample(path: Path) -> ProjectSample:
        seen.append(path)
        return ProjectSample(
            project="projA",
            timestamp=__import__("datetime").datetime(2026, 1, 1),
            date=__import__("datetime").date(2026, 1, 1),
            total_bytes=1,
            total_files=1,
            total_directories=1,
            uncompressed=UncompressedStats.zero(),
        )

    monkeypatch.setattr(ugerm, "load_overview_sample", fake_load_overview_sample)

    samples = ugerm.load_historical_project_samples([("projA", [old, prev, current])], 1)

    assert len(samples) == 1
    assert seen == [old]
