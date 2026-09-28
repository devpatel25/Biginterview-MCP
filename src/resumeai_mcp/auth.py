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
    """Load the scan page; logged in iff we stay on the portal and the landmark renders."""
    await goto(page, settings, SCAN_PATH)
    if urlparse(page.url).hostname != urlparse(settings.base_url).hostname:
        return False  # redirected to SSO / IdP
    try:
        await page.get_by_text(SCAN_LANDMARK).first.wait_for(state="visible")
    except PlaywrightTimeoutError:
        return False
    return True
