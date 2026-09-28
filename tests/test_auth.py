"""login_state against a fake Page: redirect completion, stalled redirect, missing landmark (§6.2, §13)."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from resumeai_mcp.auth import AUTH_EXPIRED, LOGGED_IN, SCAN_PATH, SITE_CHANGED, login_state
from resumeai_mcp.config import Settings
from resumeai_mcp.schemas import ToolError

BASE = "https://portal.test"
DASHBOARD = BASE + "/members/interview_dashboard/"
SCAN = BASE + SCAN_PATH
IDP = "https://login.idp.test/saml"

SETTINGS = Settings(
    base_url=BASE, data_dir=None, profile_dir=None, headless=True, browser_channel=None,
    nav_timeout_ms=1000, human_delay_min_s=0, human_delay_max_s=0,
)


class FakePage:
    """Each goto() lands on the next URL in `landings`; None = goto times out; (url, exc) = land on url, raise exc."""

    def __init__(self, landings, landmark=True):
        self.landings = list(landings)
        self.landmark = landmark
        self.url = "about:blank"

    async def goto(self, url, wait_until=None):
        landing = self.landings.pop(0)
        if landing is None:
            raise PlaywrightTimeoutError("goto timeout")
        if isinstance(landing, tuple):
            self.url, exc = landing
            raise exc
        self.url = landing

    async def wait_for_url(self, pred):
        if not pred(self.url):
            raise PlaywrightTimeoutError("wait_for_url timeout")

    def get_by_role(self, role, **kw):
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


def test_parse_scans_remaining():
    from resumeai_mcp.auth import parse_scans_remaining
    assert parse_scans_remaining("5 scans left today.") == 5
    assert parse_scans_remaining("0 scans remaining") == 0
    assert parse_scans_remaining("1 scan left") == 1
    assert parse_scans_remaining("Scan limit resets daily") is None


def test_connection_failure_raises_network_error():
    with pytest.raises(ToolError) as e:
        state(FakePage([("chrome-error://chromewebdata/", PlaywrightError("net::ERR_NAME_NOT_RESOLVED"))]))
    assert e.value.code == "network_error"
    assert "ERR_NAME_NOT_RESOLVED" in e.value.message


def test_nav_error_on_idp_is_auth_expired():
    assert state(FakePage([(IDP, PlaywrightError("net::ERR_ABORTED"))])) == AUTH_EXPIRED


class CounterPage:
    """Logged-in scan page whose counter heading read fails at `fail_at` ("count" | "inner_text")."""

    def __init__(self, fail_at, url=SCAN):
        self.fail_at, self.url = fail_at, url

    def get_by_role(self, role, **kw):
        return self

    @property
    def first(self):
        return self

    async def count(self):
        if self.fail_at == "count":
            raise PlaywrightError("Target page, context or browser has been closed")
        return 1

    async def inner_text(self):
        if self.fail_at == "inner_text":
            raise PlaywrightError("Element is not attached to the DOM")
        return "5 scans left today."


def _auth_status(monkeypatch, page):
    from resumeai_mcp import auth

    async def new_page(settings):
        return page

    async def logged_in(p, settings):
        return LOGGED_IN

    monkeypatch.setattr(auth, "new_page", new_page)
    monkeypatch.setattr(auth, "login_state", logged_in)
    return asyncio.run(auth.auth_status(SETTINGS))


def test_auth_status_reads_counter(monkeypatch):
    s = _auth_status(monkeypatch, CounterPage(fail_at=None))
    assert s.logged_in and s.scans_remaining == 5


@pytest.mark.parametrize("fail_at", ["count", "inner_text"])
def test_auth_status_counter_failure_is_network_error(monkeypatch, fail_at):
    with pytest.raises(ToolError) as e:
        _auth_status(monkeypatch, CounterPage(fail_at))
    assert e.value.code == "network_error"


@pytest.mark.parametrize("fail_at", ["count", "inner_text"])
def test_auth_status_counter_failure_on_sso_is_auth_expired(monkeypatch, fail_at):
    with pytest.raises(ToolError) as e:
        _auth_status(monkeypatch, CounterPage(fail_at, url=IDP))
    assert e.value.code == "auth_expired"


def test_auth_status_redirect_during_counter_read_is_logged_out(monkeypatch):
    page = CounterPage(fail_at=None)

    async def count():
        page.url = IDP  # redirect lands mid-read; login page has no counter
        return 0

    page.count = count
    s = _auth_status(monkeypatch, page)
    assert s.logged_in is False and s.scans_remaining is None
