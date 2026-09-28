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
    assert _run(scans.get_scan_status(_settings(tmp_path), "100")).state == state


def test_status_older_scan_reads_its_summary_page(site, tmp_path):
    summary = ('<div data-react-class="ResumeAssignmentReviewSummaryApp" data-react-props="'
               + html.escape(json.dumps({"parsedResume": {"data": {"id": "5", "attributes": {"status": "success"}}}}))
               + '"></div>')
    site([row(100)], summaries={"/members/resume_assignments/review_summary/5": summary})
    assert _run(scans.get_scan_status(_settings(tmp_path), "5")).state == "complete"


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


def test_recovery_without_match_is_unknown_state_once(site, tmp_path, resume):
    st = _settings(tmp_path)
    append_ledger(st, {"scan_id": None, "attempt_id": "a1", "resume_filename": "resume.pdf",
                       "started_at": _iso(NOW - timedelta(minutes=2))})
    site([row(200, name="other.pdf")], remaining=0)
    assert _code(_start(st, resume)) == "unknown_state"
    assert load_ledger(st)["attempt:a1"]["abandoned"] is True
    assert _code(_start(st, resume)) == "limit_reached"  # reported once, never invents an id


def test_limit_reached_before_any_form_interaction(site, tmp_path, resume):
    site([], remaining=0)
    assert _code(_start(_settings(tmp_path), resume)) == "limit_reached"


def test_invalid_inputs_touch_no_browser(site, tmp_path, resume):
    s = site([])
    assert _code(_start(_settings(tmp_path), resume, job_description="  ")) == "invalid_input"
    assert s.loads == []


# --- delete_scan ------------------------------------------------------------------------------------------------

def test_delete_refuses_latest_loop_result_without_confirm(site, tmp_path, resume):
    st = _settings(tmp_path)
    _ledger_scan(st, resume, "100", started_at=_iso(NOW - timedelta(hours=2)))
    _ledger_scan(st, resume, "101", started_at=_iso(NOW))
    site([row(101), row(100)])
    assert _code(scans.delete_scan(st, "101")) == "invalid_input"


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
