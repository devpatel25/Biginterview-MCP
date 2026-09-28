"""Login-state detection and auth_status (PLAN.md §6.2, §7.1)."""

from urllib.parse import urlparse

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import goto
from .config import Settings

SCAN_PATH = "/members/resume_assignments/scan"
# §9: the scan page is ready when this heading is visible.
SCAN_LANDMARK = "Select Scoring Guide"

LOGGED_IN = "logged_in"
AUTH_EXPIRED = "auth_expired"  # §13
SITE_CHANGED = "site_changed"  # §13


async def login_state(page: Page, settings: Settings) -> str:
    """Load the scan page; return LOGGED_IN, AUTH_EXPIRED or SITE_CHANGED. Never raises on timeout.

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
        await page.get_by_text(SCAN_LANDMARK).first.wait_for(state="visible")
    except PlaywrightTimeoutError:
        return SITE_CHANGED if on_portal(page.url) else AUTH_EXPIRED
    return LOGGED_IN if on_portal(page.url) else AUTH_EXPIRED


async def is_logged_in(page: Page, settings: Settings) -> bool:
    return await login_state(page, settings) == LOGGED_IN
