from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


def command(root: Path, args: list[str], env: dict[str, str]) -> str:
    return subprocess.check_output(args, cwd=root, env=env, text=True).strip()


@pytest.mark.parametrize("state", ["clean", "tracked", "untracked"])
def test_build_provenance_survives_sdist_without_git(tmp_path: Path, state: str) -> None:
    source = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("pyproject.toml", "hatch_build.py", ".gitignore"):
        shutil.copy2(source / name, checkout / name)
    shutil.copytree(
        source / "src",
        checkout / "src",
        ignore=shutil.ignore_patterns("__pycache__", "version.py"),
    )
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_DATE="2025-01-02T03:04:05+01:00",
        GIT_COMMITTER_DATE="2025-01-02T03:04:05+01:00",
    )
    command(checkout, ["git", "init"], env)
    command(checkout, ["git", "add", "."], env)
    command(
        checkout,
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "test",
        ],
        env,
    )
    command(checkout, ["git", "tag", "v1.2.3"], env)
    if state == "untracked":
        (checkout / "untracked.txt").write_text("untracked change\n", encoding="utf-8")
    elif state == "tracked":
        with (checkout / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("\n# tracked change\n")
    dirty = state != "clean"
    command(checkout, [sys.executable, "-m", "hatchling", "build"], env)
    wheel = next((checkout / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "durep/build_info.json" not in archive.namelist()
        version_file = archive.read("durep/version.py")
        metadata = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in ast.parse(version_file).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        }
        assert metadata["git_repo_is_dirty"] is dirty
        version = metadata["__version__"]
        assert isinstance(version, str)
        if state == "tracked":
            assert version.startswith("1.2.4.dev0+")
        else:
            assert version == "1.2.3"
    sdist = next((checkout / "dist").glob("*.tar.gz"))
    unpacked = tmp_path / "unpacked"
    with tarfile.open(sdist) as archive:
        archive.extractall(unpacked, filter="data")
    project = next(unpacked.iterdir())
    env["PATH"] = ""  # Building from the sdist and running must not need Git.
    command(project, [sys.executable, "-m", "hatchling", "build", "-t", "wheel"], env)
    rebuilt = next((project / "dist").glob("*.whl"))
    installed = tmp_path / "installed"
    with zipfile.ZipFile(rebuilt) as archive:
        assert "durep/build_info.json" not in archive.namelist()
        assert archive.read("durep/version.py") == version_file
        archive.extractall(installed)
    env["PYTHONPATH"] = str(installed)
    output = command(tmp_path, [sys.executable, "-m", "durep", "--version"], env)
    output = " ".join(output.split())
    expected = f"durep {version}" + (" (dirty repository)" if dirty else "")
    assert output == expected
