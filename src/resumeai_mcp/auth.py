"""Login-state detection and auth_status (PLAN.md §6.2, §7.1)."""

from urllib.parse import urlparse

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import goto
from .config import Settings

SCAN_PATH = "/members/resume_assignments/scan"
# §9: the scan page is ready when this heading is visible.
SCAN_LANDMARK = "Select Scoring Guide"


async def is_logged_in(page: Page, settings: Settings) -> bool:
    """Load the scan page; logged in iff the portal serves it with its landmark.

    The portal's own session cookie is session-scoped, so a fresh browser is bounced
    through SAML SSO, which re-authenticates silently while the IdP session is alive and
    then lands on the dashboard rather than the requested page. So: wait for the redirect
    chain to settle back on the portal, re-open the scan page, then check the landmark.
    If the chain stalls on the IdP (interactive login needed), the wait times out.
    """
    host = urlparse(settings.base_url).hostname
    await goto(page, settings, SCAN_PATH)
    try:
        await page.wait_for_url(
            lambda url: urlparse(url).hostname == host and urlparse(url).path.startswith("/members/")
        )
        if urlparse(page.url).path.rstrip("/") != SCAN_PATH:
            await goto(page, settings, SCAN_PATH)
        await page.get_by_text(SCAN_LANDMARK).first.wait_for(state="visible")
    except PlaywrightTimeoutError:
        return False
    return urlparse(page.url).hostname == host
