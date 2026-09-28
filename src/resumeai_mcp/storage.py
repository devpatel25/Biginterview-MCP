"""Ledger JSONL, feedback backups, HTML snapshots (PLAN.md §14)."""

from datetime import datetime
from pathlib import Path

from .config import Settings, private_dir


def save_snapshot(settings: Settings, name: str, html: str) -> Path:
    """Write page HTML to snapshots/<name>-<timestamp>.html (0600: contains PII)."""
    snap_dir = private_dir(settings.data_dir / "snapshots")
    path = snap_dir / f"{name}-{datetime.now():%Y%m%dT%H%M%S}.html"
    path.write_text(html, encoding="utf-8")
    path.chmod(0o600)
    return path
