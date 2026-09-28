"""Ledger JSONL, feedback backups, HTML snapshots (PLAN.md §14)."""

import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import Settings, private_dir


def save_snapshot(settings: Settings, name: str, html: str) -> Path:
    """Write page HTML to snapshots/<name>-<timestamp>-<random>.html.

    mkstemp creates the file exclusively with 0600 (contains PII, §6.4) and a unique suffix,
    so same-second snapshots never overwrite each other.
    """
    private_dir(settings.data_dir)  # mkdir(parents=True) alone would leave DATA_DIR at umask perms
    snap_dir = private_dir(settings.data_dir / "snapshots")
    fd, path = tempfile.mkstemp(prefix=f"{name}-{datetime.now():%Y%m%dT%H%M%S}-", suffix=".html", dir=snap_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    return Path(path)
