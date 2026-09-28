"""list_scans parsing (redacted real fixture), ledger hash-join, and pagination/cursor logic (§7.2, §10.3)."""

import asyncio
import html
import json
from pathlib import Path

import pytest

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


class FakePage:
    def __init__(self, total):
        self.total, self.loaded = total, []

    async def content(self):
        return _page_html(self.loaded[-1], self.total)


@pytest.fixture
def fake_site(monkeypatch):
    def install(total):
        page = FakePage(total)

        async def new_page(settings):
            return page

        async def nothing(*a, **kw):
            pass

        async def goto(p, settings, path):
            p.loaded.append(int(path.rsplit("=", 1)[1]))

        monkeypatch.setattr(scans, "new_page", new_page)
        monkeypatch.setattr(scans, "require_login", nothing)
        monkeypatch.setattr(scans, "human_delay", nothing)
        monkeypatch.setattr(scans, "goto", goto)
        return page
    return install


def _ids(result):
    return [int(s.scan_id) for s in result.scans]


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


@pytest.mark.parametrize("kw", [{"limit": 0}, {"limit": 51}, {"cursor": "abc"}, {"cursor": "-1"}])
def test_invalid_input(kw, tmp_path):
    with pytest.raises(ToolError) as e:
        asyncio.run(scans.list_scans(_settings(tmp_path), **kw))
    assert e.value.code == "invalid_input"


def test_corrupt_ledger_is_storage_error(tmp_path):
    (tmp_path / "ledger.jsonl").write_text("{not json\n")
    with pytest.raises(ToolError) as e:
        asyncio.run(scans.list_scans(_settings(tmp_path)))
    assert e.value.code == "storage_error"
