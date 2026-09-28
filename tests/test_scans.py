"""list_scans parsing (redacted real fixture), ledger hash-join, and pagination/cursor logic (§7.2, §10.3)."""

import asyncio
import html
import json
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from resumeai_mcp import scans
from resumeai_mcp.config import Settings
from resumeai_mcp.scans import PAGE_SIZE, parse_my_scans, to_summary
from resumeai_mcp.schemas import ToolError

FIXTURE = Path(__file__).parent / "fixtures" / "my_scans_page1.fixture.html"


def _settings(tmp_path):
    return Settings(
        base_url="https://portal.test", data_dir=tmp_path, profile_dir=tmp_path / "profile", headless=True,
        browser_channel=None, nav_timeout_ms=1000, human_delay_min_s=0, human_delay_max_s=0,
    )


def test_fixture_parses():
    rows, pagination = parse_my_scans(FIXTURE.read_text())
    assert len(rows) == 10
    assert pagination["count"] == 124 and pagination["from"] == 1
    s = to_summary(rows[0], {})
    assert s.scan_id == "900001"
    assert s.role_title == "Group Manager"
    assert s.medal == "gold"
    assert s.resume_filename == "resume_1.pdf"
    assert s.scanned_at.isoformat() == "2026-09-24T16:35:30.730000+00:00"
    # site-created row: no ledger entry → null hashes, company, guide (§7.2)
    assert (s.resume_sha256, s.jd_sha256, s.company, s.scoring_guide) == (None, None, None, None)


def test_ledger_join():
    row = {"id": 7, "attributes": {"job_title": "SWE", "document_file_name": "r.pdf", "highest_score": "processing",
                                   "created_at": None, "scoring_guide": None}}
    entry = {"scan_id": "7", "resume_sha256": "sha256:a", "jd_sha256": "sha256:b", "company": "Acme",
             "scoring_guide": "Graduate - STEM Focus"}
    s = to_summary(row, {"7": entry})
    assert (s.resume_sha256, s.jd_sha256, s.company, s.scoring_guide) == (
        "sha256:a", "sha256:b", "Acme", "Graduate - STEM Focus")
    assert s.medal is None  # non-medal score values never guessed


def test_missing_props_raises():
    with pytest.raises(ValueError):
        parse_my_scans("<html><body><ul></ul></body></html>")


def _page_html(page_num, total):
    start = (page_num - 1) * PAGE_SIZE
    ids = range(start, min(start + PAGE_SIZE, total))
    props = {
        "parsedResumes": {"data": [{"id": str(i), "attributes": {"job_title": "T", "document_file_name": f"{i}.pdf",
                                                                  "highest_score": "gold", "created_at": None}}
                                   for i in ids]},
        "paginationData": {"count": total, "page": page_num, "from": start + 1 if ids else None},
    }
    return f'<div data-react-class="{scans.SCANS_APP}" data-react-props="{html.escape(json.dumps(props))}"></div>'


PORTAL = "https://portal.test/members/resume_assignments/scans"
IDP = "https://login.idp.test/saml"


class FakePage:
    """goto(?page=N) lands on PORTAL and serves page N, unless `on_goto` overrides (url, error, html).

    error: False, True (timeout), or an exception instance to raise from goto()."""

    def __init__(self, total, on_goto=None):
        self.total, self.on_goto, self.loaded = total, on_goto, []
        self.url, self.html, self.content_error = PORTAL, "", None

    async def content(self):
        if self.content_error:
            raise self.content_error
        return self.html


@pytest.fixture
def fake_site(monkeypatch):
    def install(total, on_goto=None, logged_in=True):
        page = FakePage(total, on_goto)

        async def new_page(settings):
            return page

        async def require_login(p, settings, step):
            if not logged_in:
                raise ToolError("auth_expired", "expired", "login")

        async def nothing(*a, **kw):
            pass

        async def goto(p, settings, path):
            n = int(path.rsplit("=", 1)[1])
            p.loaded.append(n)
            p.url, timeout, p.html = (p.on_goto or (lambda n: (PORTAL, False, None)))(n)
            if p.html is None:
                p.html = _page_html(n, p.total)
            if timeout:
                raise timeout if isinstance(timeout, Exception) else PlaywrightTimeoutError("goto timeout")

        monkeypatch.setattr(scans, "new_page", new_page)
        monkeypatch.setattr(scans, "require_login", require_login)
        monkeypatch.setattr(scans, "human_delay", nothing)
        monkeypatch.setattr(scans, "goto", goto)
        return page
    return install


def _ids(result):
    return [int(s.scan_id) for s in result.scans]


def _code(settings, **kw):
    with pytest.raises(ToolError) as e:
        asyncio.run(scans.list_scans(settings, **kw))
    return e.value.code


def test_pagination_spans_site_pages(fake_site, tmp_path):
    page = fake_site(23)
    r = asyncio.run(scans.list_scans(_settings(tmp_path), limit=15, cursor="5"))
    assert _ids(r) == list(range(5, 20))
    assert page.loaded == [1, 2]
    assert r.next_cursor == "20"
    last = asyncio.run(scans.list_scans(_settings(tmp_path), limit=50, cursor=r.next_cursor))
    assert _ids(last) == [20, 21, 22]
    assert last.next_cursor is None


def test_exact_end_has_null_cursor(fake_site, tmp_path):
    fake_site(20)
    r = asyncio.run(scans.list_scans(_settings(tmp_path), limit=20))
    assert len(r.scans) == 20 and r.next_cursor is None


def test_cursor_past_end(fake_site, tmp_path):
    fake_site(5)
    r = asyncio.run(scans.list_scans(_settings(tmp_path), cursor="40"))
    assert r.scans == [] and r.next_cursor is None


@pytest.mark.parametrize("kw", [
    {"limit": 0}, {"limit": 51}, {"limit": None}, {"limit": True}, {"limit": "5"},
    {"cursor": "abc"}, {"cursor": "-1"}, {"cursor": 3}, {"cursor": "\u00b2"}, {"cursor": "9" * 5000},
])
def test_invalid_input(kw, tmp_path):
    assert _code(_settings(tmp_path), **kw) == "invalid_input"


def test_corrupt_ledger_is_storage_error(fake_site, tmp_path):
    fake_site(5)
    (tmp_path / "ledger.jsonl").write_text("{not json\n")
    assert _code(_settings(tmp_path)) == "storage_error"


def test_auth_gate_runs_before_ledger(fake_site, tmp_path):
    fake_site(5, logged_in=False)
    (tmp_path / "ledger.jsonl").write_text("{not json\n")
    assert _code(_settings(tmp_path)) == "auth_expired"


def test_session_expires_between_precheck_and_load(fake_site, tmp_path):
    fake_site(5, on_goto=lambda n: (IDP, False, "<html>login</html>"))
    assert _code(_settings(tmp_path)) == "auth_expired"


def test_navigation_timeout_off_portal_is_auth_expired(fake_site, tmp_path):
    fake_site(5, on_goto=lambda n: (IDP, True, ""))
    assert _code(_settings(tmp_path)) == "auth_expired"


def test_navigation_timeout_on_portal_is_site_changed_with_snapshot(fake_site, tmp_path):
    fake_site(5, on_goto=lambda n: (PORTAL, True, "<html>half-loaded</html>"))
    assert _code(_settings(tmp_path)) == "site_changed"
    assert list((tmp_path / "snapshots").glob("list-scans-p1-*.html"))


@pytest.mark.parametrize("attributes", [[], None, "x"])
def test_malformed_row_is_site_changed(fake_site, tmp_path, attributes):
    props = {"parsedResumes": {"data": [{"id": "1", "attributes": attributes}]},
             "paginationData": {"count": 1, "from": 1}}
    bad = f'<div data-react-class="{scans.SCANS_APP}" data-react-props="{html.escape(json.dumps(props))}"></div>'
    fake_site(1, on_goto=lambda n: (PORTAL, False, bad))
    assert _code(_settings(tmp_path)) == "site_changed"


def test_connection_failure_is_network_error(fake_site, tmp_path):
    fake_site(5, on_goto=lambda n: ("chrome-error://chromewebdata/", PlaywrightError("net::ERR_CONNECTION_REFUSED"), ""))
    assert _code(_settings(tmp_path)) == "network_error"


def test_nav_error_on_sso_host_is_auth_expired(fake_site, tmp_path):
    fake_site(5, on_goto=lambda n: (IDP, PlaywrightError("net::ERR_ABORTED"), ""))
    assert _code(_settings(tmp_path)) == "auth_expired"


def test_content_failure_is_network_error(fake_site, tmp_path):
    page = fake_site(5)
    page.content_error = PlaywrightError("Target page, context or browser has been closed")
    assert _code(_settings(tmp_path)) == "network_error"
