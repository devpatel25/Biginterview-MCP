"""Pydantic v2 models for every tool input/output (PLAN.md §8). Phases 1/2."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Medal = Literal["gold", "silver", "bronze"]

# §13 error catalog.
ErrorCode = Literal[
    "auth_expired", "limit_reached", "scan_failed", "scan_timeout", "unknown_state", "not_found",
    "site_changed", "invalid_input", "storage_error", "profile_in_use", "network_error",
]


class ToolError(Exception):
    """A §13 error; server.py (Phase 4) turns it into the §7.0 error envelope."""

    def __init__(self, code: ErrorCode, message: str, hint: str):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


class AuthStatus(BaseModel):
    logged_in: bool
    scans_remaining: int | None
    account_email: str | None
    checked_at: datetime


class ScanSummary(BaseModel):
    scan_id: str
    role_title: str
    company: str | None
    scoring_guide: str | None
    scanned_at: datetime | None
    medal: Medal | None
    resume_filename: str
    resume_sha256: str | None  # ledger-only (§7.2)
    jd_sha256: str | None  # ledger-only (§7.2)


class ScanList(BaseModel):
    scans: list[ScanSummary]
    next_cursor: str | None
