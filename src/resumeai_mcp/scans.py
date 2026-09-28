"""list_scans, start_scan, get_scan_status, delete_scan (PLAN.md §7.2-7.6, §10). Phases 1/3."""

import json
from html.parser import HTMLParser

from .auth import fetch_page, require_login, site_changed_error
from .browser import new_page
from .config import Settings
from .schemas import ScanList, ScanSummary, ToolError
from .storage import load_ledger

SCANS_PATH = "/members/resume_assignments/scans"
# My Scans is a React app; its rows are embedded as JSON in this element's data-react-props
# (§9 amendment 2026-09-28: medal and ISO timestamps are only reliably available there).
SCANS_APP = "UserResumeAssignmentScansApp"
PAGE_SIZE = 10  # site's fixed page size; ?page=N selects the page server-side
MEDALS = ("gold", "silver", "bronze")


class _PropsFinder(HTMLParser):
    def __init__(self, app: str):
        super().__init__()
        self.app, self.props = app, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.props is None and a.get("data-react-class") == self.app:
            self.props = a.get("data-react-props")


def react_props(html: str, app: str) -> dict:
    """The JSON props of the portal's React app `app` (§9 amendment 2026-09-28). ValueError if absent."""
    finder = _PropsFinder(app)
    finder.feed(html)
    if finder.props is None:
        raise ValueError(f"{app} props not found")
    props = json.loads(finder.props)
    if not isinstance(props, dict):
        raise TypeError(f"{app} props is not an object")
    return props


def parse_my_scans(html: str) -> tuple[list[dict], dict]:
    """(rows, paginationData) from My Scans HTML. Raises ValueError/KeyError/TypeError if the shape changed."""
    props = react_props(html, SCANS_APP)
    rows, pagination = props["parsedResumes"]["data"], props["paginationData"]
    if not isinstance(rows, list) or not isinstance(pagination["count"], int):
        raise TypeError("unexpected My Scans props shape")
    return rows, pagination


def to_summary(row: dict, ledger: dict[str, dict]) -> ScanSummary:
    """Site row → ScanSummary. Hashes (and company/guide the row lacks) come only from the ledger (§7.2)."""
    a, scan_id = row["attributes"], str(row["id"])
    if not isinstance(a, dict):
        raise TypeError("row attributes is not an object")
    entry = ledger.get(scan_id, {})
    medal = a.get("highest_score")
    return ScanSummary(
        scan_id=scan_id,
        role_title=a.get("job_title") or "",
        company=entry.get("company"),
        scoring_guide=a.get("scoring_guide") or entry.get("scoring_guide"),
        scanned_at=a.get("created_at"),
        medal=medal if medal in MEDALS else None,
        resume_filename=a["document_file_name"],
        resume_sha256=entry.get("resume_sha256"),
        jd_sha256=entry.get("jd_sha256"),
    )


async def list_scans(settings: Settings, limit: int = 20, cursor: str | None = None) -> ScanList:
    """§7.2 / §10.3. cursor is the absolute row offset as a string (opaque to the agent)."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ToolError("invalid_input", "limit must be an integer 1-50.", "Retry with 1 <= limit <= 50.")
    if cursor is not None and not (
        isinstance(cursor, str) and cursor.isascii() and cursor.isdigit() and len(cursor) <= 9
    ):
        raise ToolError("invalid_input", "Malformed cursor.", "Pass next_cursor exactly as returned, or null.")
    offset = int(cursor or 0)
    page = await new_page(settings)
    await require_login(page, settings, "list-scans")  # §6.2: auth gate before any other work
    ledger = load_ledger(settings)

    scans: list[ScanSummary] = []
    total = None
    while len(scans) < limit and (total is None or offset + len(scans) < total):
        n = offset + len(scans)
        page_num = n // PAGE_SIZE + 1
        step = f"list-scans-p{page_num}"
        content = await fetch_page(page, settings, f"{SCANS_PATH}?page={page_num}", step)
        try:
            site_rows, pagination = parse_my_scans(content)
            if site_rows and pagination.get("from") != (page_num - 1) * PAGE_SIZE + 1:
                raise ValueError("page size changed")
            chunk = site_rows[n - (page_num - 1) * PAGE_SIZE:][: limit - len(scans)]
            chunk = [to_summary(r, ledger) for r in chunk]  # pydantic ValidationError is a ValueError
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise await site_changed_error(page, settings, step, f"My Scans data ({type(e).__name__})") from e
        total = pagination["count"]
        if not chunk:
            break
        scans += chunk

    end = offset + len(scans)
    return ScanList(
        scans=scans,
        next_cursor=str(end) if total is not None and end < total else None,
    )
