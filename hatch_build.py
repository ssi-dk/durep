"""Capture Git provenance once, preserving it through sdist-to-wheel builds."""

import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


def git(root: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        root = Path(self.root)
        relative_path = "src/durep/version.py"
        destination = root / relative_path
        # A .git file also covers worktrees. Do not inspect a parent repository
        # when rebuilding an unpacked sdist inside another checkout.
        if (root / ".git").exists():
            metadata = {
                "__version__": self.metadata.version,
                "git_repo_is_dirty": bool(
                    git(root, ["status", "--porcelain", "--untracked-files=normal"])
                ),
            }
            destination.write_text(
                "# Generated at build time; do not edit or commit.\n"
                + "".join(f"{name} = {value!r}\n" for name, value in metadata.items()),
                encoding="utf-8",
            )
        elif not destination.is_file():
            raise RuntimeError("Build from a Git checkout or an sdist containing version.py")
        build_data["artifacts"].append(f"/{relative_path}")
