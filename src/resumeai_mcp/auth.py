"""Login-state detection and auth_status (PLAN.md §6.2, §7.1)."""

import re
from datetime import datetime
from urllib.parse import urlparse

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import goto, new_page, snapshot
from .config import Settings
from .schemas import AuthStatus, ToolError

SCAN_PATH = "/members/resume_assignments/scan"
# §9: the scan page is ready when this heading is visible.
SCAN_LANDMARK = "Select Scoring Guide"
# §9 allowance counter; live page shows the heading "5 scans left today.".
REMAINING_RE = re.compile(r"(\d+)\s*(?:scans?\s+)?(?:remaining|left)", re.I)

LOGGED_IN = "logged_in"
AUTH_EXPIRED = "auth_expired"  # §13
SITE_CHANGED = "site_changed"  # §13


async def login_state(page: Page, settings: Settings) -> str:
    """Load the scan page; return LOGGED_IN, AUTH_EXPIRED or SITE_CHANGED.

    Playwright timeouts are classified, never raised; other errors propagate.
    The portal's own session cookie is session-scoped, so a fresh browser is bounced
    through SAML SSO, which re-authenticates silently while the IdP session is alive and
    then lands on the dashboard rather than the requested page. So: wait for the redirect
    chain to settle back on the portal, re-open the scan page, then check the landmark.
    On any navigation/wait timeout the final URL decides: still off the portal (stalled on
    the IdP/login page) → AUTH_EXPIRED; on the portal but no landmark → SITE_CHANGED.
    """
    host = urlparse(settings.base_url).hostname

    def on_portal(url: str) -> bool:
        u = urlparse(url)
        return u.hostname == host and u.path.startswith("/members/")

    try:
        await goto(page, settings, SCAN_PATH)
        await page.wait_for_url(on_portal)
        if urlparse(page.url).path.rstrip("/") != SCAN_PATH:
            await goto(page, settings, SCAN_PATH)
        await page.get_by_role("heading", name=SCAN_LANDMARK, exact=True).wait_for(state="visible")
    except PlaywrightTimeoutError:
        return SITE_CHANGED if on_portal(page.url) else AUTH_EXPIRED
    return LOGGED_IN if on_portal(page.url) else AUTH_EXPIRED


async def is_logged_in(page: Page, settings: Settings) -> bool:
    return await login_state(page, settings) == LOGGED_IN


async def require_login(page: Page, settings: Settings, step: str) -> None:
    """§6.2 pre-check for every site-touching tool: raise auth_expired / site_changed (+ snapshot)."""
    await raise_for_state(page, settings, await login_state(page, settings), step)


async def raise_for_state(page: Page, settings: Settings, state: str, step: str) -> None:
    if state == AUTH_EXPIRED:
        raise ToolError("auth_expired", "Big Interview session expired (SSO login required).",
                        "Stop. Ask the user to run scripts/login.py, then retry.")
    if state == SITE_CHANGED:
        path = await snapshot(page, settings, step)
        raise ToolError("site_changed", f"Scan page landmark '{SCAN_LANDMARK}' not found.",
                        f"Stop. Inspect snapshot {path.name}; selectors need a fix.")


def parse_scans_remaining(text: str) -> int | None:
    m = REMAINING_RE.search(text)
    return int(m.group(1)) if m else None


async def auth_status(settings: Settings) -> AuthStatus:
    """§7.1. logged_in=false is data, not an error; the counter is null when not rendered."""
    page = await new_page(settings)
    state = await login_state(page, settings)
    if state == SITE_CHANGED:
        await raise_for_state(page, settings, state, "auth-status")
    remaining = None
    if state == LOGGED_IN:
        counter = page.get_by_role("heading", name=REMAINING_RE)
        if await counter.count():
            remaining = parse_scans_remaining(await counter.first.inner_text())
    return AuthStatus(
        logged_in=state == LOGGED_IN,
        scans_remaining=remaining,
        account_email=None,  # not rendered on the scan page; §7.1 says null when not visible
        checked_at=datetime.now().astimezone(),
    )
