from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from repository_scanner import build_tree, scan_repository_root

MAX_FILE_READ_BYTES = int(os.getenv("REPOSITORY_MAX_FILE_READ_BYTES", str(1024 * 1024)))


class RepositoryLimitExceeded(Exception):
    """Deprecated compatibility exception for callers of the legacy scanner."""


def clone_repository(url: str, destination: Path) -> Path:
    """Clone a validated repository without invoking a shell or running its code."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", url, str(destination)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode:
        shutil.rmtree(destination, ignore_errors=True)
        message = result.stderr.strip() or "Git clone failed"
        raise RuntimeError(message[-1000:])
    return destination


def scan_repository(root: Path) -> dict[str, Any]:
    """Compatibility wrapper around the Phase 2 scanner for existing callers."""
    entries, statistics, _languages, extensions = scan_repository_root(root)
    return {"statistics": statistics, "extensions": extensions, "tree": build_tree(entries)}
