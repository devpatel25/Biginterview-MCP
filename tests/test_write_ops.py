"""get_scan_status / start_scan / delete_scan decision logic against a fake site (§7.3, §7.4, §7.6, §10.2).
The §9 UI clicks themselves are covered by the manual E2E checklist (§16), not here."""

import asyncio
import html
import json
from datetime import datetime, timedelta, timezone

import pytest

from resumeai_mcp import scans
from resumeai_mcp.config import Settings
from resumeai_mcp.schemas import ToolError
from resumeai_mcp.storage import append_ledger, load_ledger

NOW = datetime.now(timezone.utc)
PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nstream\nBT (Jane Doe Engineer) Tj ET\nendstream\nendobj\n%%EOF\n"


def _settings(tmp_path):
    return Settings(base_url="https://portal.test", data_dir=tmp_path / "data", profile_dir=tmp_path / "profile",
                    headless=True, browser_channel=None, nav_timeout_ms=1000, human_delay_min_s=0,
                    human_delay_max_s=0)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def row(i, status="success", name="resume.pdf", title="AI Intern", created=NOW, medal="gold"):
    return {"id": str(i), "attributes": {"status": status, "document_file_name": name, "job_title": title,
                                         "created_at": _iso(created), "highest_score": medal}}


def my_scans_html(rows, count=None):
    props = {"parsedResumes": {"data": rows}, "paginationData": {"count": count or len(rows), "from": 1}}
    return f'<div data-react-class="{scans.SCANS_APP}" data-react-props="{html.escape(json.dumps(props))}"></div>'


class FakeSite:
    def __init__(self, rows, remaining=5, summaries=None):
        self.rows, self.remaining, self.summaries, self.loads = rows, remaining, summaries or {}, []
        self.url = "https://portal.test/members/resume_assignments/scan"

    async def content(self):
        return "<html>page</html>"


@pytest.fixture
def site(monkeypatch):
    def install(rows, remaining=5, summaries=None):
        s = FakeSite(rows, remaining, summaries)

        async def new_page(settings):
            return s

        async def nothing(*a, **kw):
            pass

        async def remaining_(page, settings):
            return s.remaining

        async def fetch_page(page, settings, path, step):
            s.loads.append(path)
            s.url = "https://portal.test" + path
            if path.startswith(scans.SCANS_PATH):
                return my_scans_html(s.rows)
            if path in s.summaries:
                return s.summaries[path]
            s.url = "https://portal.test/members/resume_dashboard"  # unknown id → redirected away
            return "<html></html>"

        async def submit(*a, **kw):
            raise AssertionError("must not submit a new scan in this scenario")

        for name, fn in (("new_page", new_page), ("require_login", nothing), ("read_scans_remaining", remaining_),
                         ("fetch_page", fetch_page), ("_submit", submit)):
            monkeypatch.setattr(scans, name, fn)
        monkeypatch.setattr(scans, "site_changed_error", _fake_site_changed)
        return s
    return install


async def _fake_site_changed(page, settings, step, what, cause=None):
    return ToolError("site_changed", what, "snapshot")


def _run(coro):
    return asyncio.run(coro)


def _code(coro):
    with pytest.raises(ToolError) as e:
        _run(coro)
    return e.value.code


# --- get_scan_status ----------------------------------------------------------------------------------------------

def test_status_complete_records_completion_in_ledger(site, tmp_path):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": "100", "started_at": _iso(NOW), "completed_at": None, "medal": None})
    site([row(100, medal="silver")], remaining=3)
    s = _run(scans.get_scan_status(st, "100"))
    assert (s.state, s.scans_remaining) == ("complete", 3)
    entry = load_ledger(st)["100"]
    assert entry["completed_at"] and entry["medal"] == "silver"


@pytest.mark.parametrize("status,state", [("processing", "scanning"), ("pending", "queued"), ("failed", "failed"),
                                          ("brand_new_value", "unknown")])
def test_status_mapping(site, tmp_path, status, state):
    site([row(100, status=status)])
    s = _run(scans.get_scan_status(_settings(tmp_path), "100"))
    assert s.state == state
    if state == "unknown":  # §7.4: snapshot saved and named in the hint
        assert "inspect snapshot 100-status-" in s.hint
        assert list((tmp_path / "data" / "snapshots").glob("100-status-*.html"))
    else:
        assert s.hint is None


def test_status_older_scan_reads_its_summary_page_and_records_medal(site, tmp_path):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": "5", "started_at": _iso(NOW), "completed_at": None, "medal": None})
    attrs = {"status": "success", "highest_score": "bronze"}
    summary = ('<div data-react-class="ResumeAssignmentReviewSummaryApp" data-react-props="'
               + html.escape(json.dumps({"parsedResume": {"data": {"id": "5", "attributes": attrs}}})) + '"></div>')
    site([row(100)], summaries={"/members/resume_assignments/review_summary/5": summary})
    assert _run(scans.get_scan_status(st, "5")).state == "complete"
    assert load_ledger(st)["5"]["medal"] == "bronze"


def test_status_unknown_id_is_not_found_but_ledgered_id_is_unknown(site, tmp_path):
    st = _settings(tmp_path)
    site([row(100)])
    assert _code(scans.get_scan_status(st, "5")) == "not_found"
    append_ledger(st, {"scan_id": "5", "started_at": _iso(NOW)})
    assert _run(scans.get_scan_status(st, "5")).state == "unknown"


# --- start_scan -------------------------------------------------------------------------------------------------

@pytest.fixture
def resume(tmp_path):
    p = tmp_path / "resume.pdf"
    p.write_bytes(PDF)
    return p


def _start(st, resume, **over):
    args = {"resume_path": str(resume), "job_title": "AI Intern", "company": "Acme", "job_description": "Build ML"}
    return scans.start_scan(st, **{**args, **over})


def _ledger_scan(st, resume, scan_id, **over):
    entry = {"scan_id": scan_id, "resume_filename": resume.name, "resume_sha256": scans.sha256_file(resume),
             "jd_sha256": scans.jd_sha256("Build ML"), "role_title": "AI Intern", "company": "Acme",
             "scoring_guide": "Graduate - STEM Focus", "started_at": _iso(NOW - timedelta(hours=1)),
             "completed_at": _iso(NOW), "deleted_at": None}
    append_ledger(st, {**entry, **over})


def test_reuse_returns_existing_scan_without_consuming_allowance(site, tmp_path, resume):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100")
    site([row(100)], remaining=2)
    r = _run(_start(st, resume))
    assert (r.scan_id, r.reused, r.state, r.scans_remaining) == ("100", True, "complete", 2)


@pytest.mark.parametrize("over", [{"company": "Other"}, {"scoring_guide": "PhD Student"}, {"job_title": "SWE"},
                                  {"job_description": "Different JD"}])
def test_no_reuse_when_any_key_field_differs(site, tmp_path, resume, over):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100")
    site([row(100)], remaining=0)  # would need a fresh scan → limit reached proves no reuse happened
    assert _code(_start(st, resume, **over)) == "limit_reached"


def test_deleted_scan_is_not_reused(site, tmp_path, resume):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100", deleted_at=_iso(NOW))
    site([row(100)], remaining=0)
    assert _code(_start(st, resume)) == "limit_reached"


def test_one_scan_in_flight(site, tmp_path, resume):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100", started_at=_iso(NOW), completed_at=None, company="Other")
    site([row(100, status="processing")])
    assert _code(_start(st, resume)) == "invalid_input"


def test_recovery_adopts_matching_row(site, tmp_path, resume):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": None, "attempt_id": "a1", "resume_filename": "resume.pdf",
                       "started_at": _iso(NOW - timedelta(minutes=2))})
    site([row(200, created=NOW - timedelta(minutes=1))], remaining=0)
    assert _code(_start(st, resume, company="New")) == "limit_reached"  # proceeds past recovery
    ledger = load_ledger(st)
    assert ledger["200"]["recovered"] is True
    assert ledger["attempt:a1"]["resolved_scan_id"] == "200"


def test_recovery_keeps_attempt_pending_until_the_row_appears(site, tmp_path, resume):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": None, "attempt_id": "a1", "resume_filename": "resume.pdf",
                       "started_at": _iso(NOW - timedelta(minutes=2)), "known_ids": ["150"]})
    s = site([row(150, created=NOW - timedelta(minutes=2, seconds=20))], remaining=0)  # pre-existing, excluded
    assert _code(_start(st, resume)) == "unknown_state"  # blocks a duplicate submission
    assert not load_ledger(st)["attempt:a1"].get("abandoned")  # still recoverable
    s.rows = [row(200, created=NOW - timedelta(minutes=1)), *s.rows]  # the delayed row shows up
    assert _code(_start(st, resume, company="New")) == "limit_reached"
    assert load_ledger(st)["200"]["recovered"] is True


def test_recovery_abandons_only_after_its_window(site, tmp_path, resume):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": None, "attempt_id": "a1", "resume_filename": "resume.pdf",
                       "started_at": _iso(NOW - timedelta(minutes=12))})
    site([row(200, name="other.pdf")], remaining=0)
    assert _code(_start(st, resume)) == "unknown_state"
    assert load_ledger(st)["attempt:a1"]["abandoned"] is True
    assert _code(_start(st, resume)) == "limit_reached"  # reported, then resolved; never invents an id


def test_limit_reached_before_any_form_interaction(site, tmp_path, resume):
    site([], remaining=0)
    assert _code(_start(_settings(tmp_path), resume)) == "limit_reached"


def test_invalid_inputs_touch_no_browser(site, tmp_path, resume):
    s = site([])
    assert _code(_start(_settings(tmp_path), resume, job_description="  ")) == "invalid_input"
    assert s.loads == []


# --- delete_scan ------------------------------------------------------------------------------------------------

def test_delete_backs_up_first_then_refuses_latest_without_confirm(site, tmp_path, resume, monkeypatch):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100", started_at=_iso(NOW - timedelta(hours=2)))
    _ledger_scan(st, resume, "101", started_at=_iso(NOW))
    s = site([row(101), row(100)])
    read = []

    async def fake_read(page, settings, scan_id, ledger):
        read.append(scan_id)
        _write_backup(settings, scan_id)

    monkeypatch.setattr(scans, "read_feedback", fake_read)
    assert _code(scans.delete_scan(st, "101")) == "invalid_input"
    assert read == ["101"]  # §7.6: backup happened before the guard
    assert not any(p.startswith(scans.SCANS_PATH) for p in s.loads)


def _write_backup(settings, scan_id):
    from pathlib import Path
    from resumeai_mcp.feedback import parse_feedback
    from resumeai_mcp.storage import backup_feedback
    fixture = Path(__file__).parent / "fixtures" / "feedback_gold_bronze.fixture.html"
    fb = parse_feedback(fixture.read_text(), "900101").model_copy(update={"scan_id": scan_id})
    backup_feedback(settings, fb)


def test_backup_for_another_scan_is_not_trusted(tmp_path):
    from resumeai_mcp.storage import feedback_backup
    st = _settings(tmp_path)
    _write_backup(st, "900101")
    (st.data_dir / "feedback" / "900101.json").rename(st.data_dir / "feedback" / "900102.json")
    assert feedback_backup(st, "900102") is None
    _write_backup(st, "900101")
    assert feedback_backup(st, "900101") is not None


def test_delete_aborts_when_backup_fails(site, tmp_path, monkeypatch):
    s = site([row(100)])

    async def failing_read(*a, **kw):
        raise ToolError("storage_error", "disk full", "stop")

    monkeypatch.setattr(scans, "read_feedback", failing_read)
    assert _code(scans.delete_scan(_settings(tmp_path), "100", confirm=True)) == "storage_error"
    assert not any(p.startswith(scans.SCANS_PATH) for p in s.loads)  # never reached the delete UI


@pytest.mark.parametrize("kw", [{"scan_id": "abc"}, {"scan_id": "1", "confirm": "yes"}])
def test_delete_input_validation(tmp_path, kw):
    assert _code(scans.delete_scan(_settings(tmp_path), **kw)) == "invalid_input"


# --- _submit: §9 landmarks and id capture against a fake form ---------------------------------------------------

from playwright.async_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402

GUIDE = "Graduate - STEM Focus"
FORM = {("searchbox", "Search for a score guide"), ("button", f"Select {GUIDE}"), ("radio", "Add Job Description"),
        ("textbox", "Role / Position"), ("textbox", "Company Name"), ("textbox", "Job Description"),
        ("button", "Scan Resume")}


class FakeLocator:
    def __init__(self, form, key):
        self.form, self.key = form, key

    async def wait_for(self, state=None, timeout=None):
        if self.key not in self.form.controls:
            raise PlaywrightTimeoutError("not visible")

    async def fill(self, value):
        self.form.filled[self.key[1]] = value

    async def check(self):
        pass

    async def click(self):
        if self.key == ("button", f"Select {GUIDE}"):
            self.form.controls |= {("heading", GUIDE), ("button", "Remove")}
        if self.key == ("button", "Scan Resume"):
            self.form.on_scan()

    async def count(self):
        return 1 if self.key in self.form.controls else 0

    @property
    def first(self):
        return self

    async def set_input_files(self, path):
        from pathlib import Path
        self.form.controls.add(("heading", Path(path).name))


class FakeForm:
    def __init__(self, missing=(), new_row=None):
        self.controls = (FORM | {("file", "input")}) - set(missing)
        self.filled, self.rows, self.new_row = {}, [], new_row
        self.url = "https://portal.test/members/resume_assignments/scan"

    def get_by_role(self, role, name=None, exact=None):
        return FakeLocator(self, (role, name))

    def locator(self, selector):
        return FakeLocator(self, ("file", "input"))

    def on_scan(self):
        if self.new_row:
            self.rows.insert(0, self.new_row())

    async def content(self):
        return "<html>form</html>"


@pytest.fixture
def form(monkeypatch):
    def install(**kw):
        f = FakeForm(**kw)

        async def fetch_page(page, settings, path, step):
            return my_scans_html(f.rows) if path.startswith(scans.SCANS_PATH) else "<html>scan page</html>"

        async def nothing(*a, **k):
            pass

        monkeypatch.setattr(scans, "fetch_page", fetch_page)
        monkeypatch.setattr(scans, "human_delay", nothing)
        monkeypatch.setattr(scans, "CAPTURE_WINDOW_S", 0.3)
        return f
    return install


def _submit(st, page, resume, known=frozenset()):
    return scans._submit(page, st, resume, "AI Intern", "Acme", "Build ML", GUIDE, "sha256:r", "sha256:j",
                         "start-scan", set(known))


def test_submit_happy_path_ledgers_attempt_then_scan(form, tmp_path, resume):
    st = _settings(tmp_path)
    f = form(new_row=lambda: row(300, status="processing", created=datetime.now(timezone.utc)))
    assert _run(_submit(st, f, resume)) == ("300", "scanning")
    assert f.filled == {"Search for a score guide": GUIDE, "Role / Position": "AI Intern", "Company Name": "Acme",
                        "Job Description": "Build ML"}
    ledger = load_ledger(st)
    attempt = next(v for k, v in ledger.items() if k.startswith("attempt:"))
    assert attempt["resolved_scan_id"] == "300" and ledger["300"]["resume_sha256"] == "sha256:r"


@pytest.mark.parametrize("missing", [("radio", "Add Job Description"), ("textbox", "Company Name"),
                                     ("textbox", "Job Description"), ("file", "input"), ("button", "Scan Resume")])
def test_missing_control_is_site_changed_with_snapshot_and_no_attempt(form, tmp_path, resume, missing):
    st = _settings(tmp_path)
    f = form(missing=[missing])
    assert _code(_submit(st, f, resume)) == "site_changed"
    assert list((st.data_dir / "snapshots").glob("start-scan-*.html"))
    assert load_ledger(st) == {}  # nothing was submitted, so no attempt recorded


def test_unknown_guide_is_invalid_input(form, tmp_path, resume):
    f = form(missing=[("button", f"Select {GUIDE}")])
    assert _code(_submit(_settings(tmp_path), f, resume)) == "invalid_input"


def test_captured_failed_row_is_scan_failed_and_keeps_id(form, tmp_path, resume):
    st = _settings(tmp_path)
    f = form(new_row=lambda: row(301, status="failed", created=datetime.now(timezone.utc)))
    with pytest.raises(ToolError) as e:
        _run(_submit(st, f, resume))
    assert e.value.code == "scan_failed" and "301" in e.value.hint
    assert "301" in load_ledger(st)


def test_preexisting_same_file_row_is_never_captured(form, tmp_path, resume):
    st = _settings(tmp_path)
    f = form()
    f.rows = [row(299, name="resume.pdf", created=datetime.now(timezone.utc) - timedelta(seconds=20))]
    assert _code(_submit(st, f, resume, known={"299"})) == "unknown_state"
    assert all(k.startswith("attempt:") for k in load_ledger(st))  # attempt stays pending for recovery


def test_site_operations_never_interleave():
    from resumeai_mcp.browser import site_operation
    events = []

    @site_operation
    async def op(name):
        events.append(f"{name}-start")
        await asyncio.sleep(0.01)
        events.append(f"{name}-end")

    async def both():
        await asyncio.gather(op("a"), op("b"))

    asyncio.run(both())
    assert events in (["a-start", "a-end", "b-start", "b-end"], ["b-start", "b-end", "a-start", "a-end"])
