"""login_state against a fake Page: redirect completion, stalled redirect, missing landmark (§6.2, §13)."""

import asyncio

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from resumeai_mcp.auth import AUTH_EXPIRED, LOGGED_IN, SCAN_PATH, SITE_CHANGED, login_state
from resumeai_mcp.config import Settings

BASE = "https://portal.test"
DASHBOARD = BASE + "/members/interview_dashboard/"
SCAN = BASE + SCAN_PATH
IDP = "https://login.idp.test/saml"

SETTINGS = Settings(
    base_url=BASE, data_dir=None, profile_dir=None, headless=True, browser_channel=None,
    nav_timeout_ms=1000, human_delay_min_s=0, human_delay_max_s=0,
)


class FakePage:
    """Each goto() lands on the next URL in `landings`; a URL of None means that goto times out."""

    def __init__(self, landings, landmark=True):
        self.landings = list(landings)
        self.landmark = landmark
        self.url = "about:blank"

    async def goto(self, url, wait_until=None):
        landing = self.landings.pop(0)
        if landing is None:
            raise PlaywrightTimeoutError("goto timeout")
        self.url = landing

    async def wait_for_url(self, pred):
        if not pred(self.url):
            raise PlaywrightTimeoutError("wait_for_url timeout")

    def get_by_text(self, text):
        return self

    @property
    def first(self):
        return self

    async def wait_for(self, state=None):
        if not self.landmark:
            raise PlaywrightTimeoutError("landmark timeout")


def state(page):
    return asyncio.run(login_state(page, SETTINGS))


def test_saml_redirect_completes_to_dashboard_then_scan():
    assert state(FakePage([DASHBOARD, SCAN])) == LOGGED_IN


def test_direct_scan_page():
    assert state(FakePage([SCAN])) == LOGGED_IN


def test_redirect_stalled_on_idp():
    assert state(FakePage([IDP])) == AUTH_EXPIRED


def test_initial_goto_timeout_does_not_raise():
    assert state(FakePage([None])) == AUTH_EXPIRED


def test_second_goto_timeout_does_not_raise():
    assert state(FakePage([DASHBOARD, None])) == SITE_CHANGED


def test_portal_served_but_landmark_missing():
    assert state(FakePage([SCAN], landmark=False)) == SITE_CHANGED
