from __future__ import annotations

from pathlib import Path

from ..context import Context, TargetUnavailable
from ..config import RepoTarget

README_NAMES = ("README.md", "readme.md", "Readme.md", "README.markdown", "README.rst", "README.txt", "README")


def find_readme(ctx: Context, repo: RepoTarget) -> tuple[Path, str]:
    """Return (path, text) of the root README. Raises TargetUnavailable."""
    root = ctx.repo_dir(repo)
    for name in README_NAMES:
        path = root / name
        if path.is_file():
            return path, path.read_text(encoding="utf-8", errors="replace")
    raise TargetUnavailable(f"no README at the root of {repo.slug}")
