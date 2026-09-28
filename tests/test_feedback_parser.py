"""feedback.py against redacted real review_summary fixtures + synthetic edge cases (§8, §15 Phase 2)."""

import asyncio
import html
import json
import os
import re
import stat
from pathlib import Path

import pytest

from resumeai_mcp import auth, feedback
from resumeai_mcp.config import Settings
from resumeai_mcp.feedback import SUMMARY_APP, parse_feedback
from resumeai_mcp.schemas import ToolError

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name):
    return (FIXTURES / f"feedback_{name}.fixture.html").read_text()


def _flagged(fb):
    return {c: [(x.name, x.status) for x in getattr(fb.categories, c).criteria if x.status != "perfect"]
            for c in ("readability", "credibility", "ats_fit", "format")}


def test_gold_with_bronze_items():
    fb = parse_feedback(_html("gold_bronze"), "900101")
    assert (fb.medal, fb.partial, fb.scoring_guide, fb.role_title) == ("gold", False, "Graduate - STEM Focus", "Group Manager")
    assert {c: getattr(fb.categories, c).badge for c in ("readability", "credibility", "ats_fit", "format")} == {
        "readability": "gold", "credibility": "gold", "ats_fit": "silver", "format": "silver"}
    assert [len(getattr(fb.categories, c).criteria) for c in ("readability", "credibility", "ats_fit", "format")] == [4, 9, 7, 6]
    assert _flagged(fb) == {  # exactly what the live summary page listed
        "readability": [],
        "credibility": [("Experience Description", "needs_work"), ("Education Details", "needs_work")],
        "ats_fit": [("Job Title Match", "needs_work"), ("Location Matching", "needs_work")],
        "format": [("Font Size & Choice", "needs_work"), ("Date Format", "needs_work")],
    }
    assert len(fb.action_items) == 6


def test_silver_overall():
    fb = parse_feedback(_html("silver"), "900102")
    assert fb.medal == "silver" and fb.categories.credibility.badge == "silver"
    assert ("Experience Details", "needs_work") in _flagged(fb)["credibility"]


def test_silver_leaves_are_warning_and_flagged_under_gold():
    fb = parse_feedback(_html("gold_with_silver_leaves"), "900103")
    assert all(getattr(fb.categories, c).badge == "gold" for c in ("readability", "credibility", "ats_fit", "format"))
    flagged = _flagged(fb)
    assert flagged["readability"] == [("Spelling & Grammar", "warning")]
    assert ("Keyword Matching", "warning") in flagged["ats_fit"]
    # §8: red flags under a Gold badge still surface as action items, marked as such
    items = {(a.category, a.item.split(" (")[0].split(":")[0]): a for a in fb.action_items}
    assert "flagged although the category badge is Gold" in items[("readability", "Spelling & Grammar")].item
    assert len(fb.action_items) == sum(len(v) for v in flagged.values())
    kw = next(c for c in fb.categories.readability.criteria if c.name == "Spelling & Grammar")
    assert kw.detail.startswith("Almost There")  # raw site label kept


def test_keywords_match_site_lists():
    ats = parse_feedback(_html("gold_bronze"), "900101").categories.ats_fit
    # first entries exactly as the live ATS Fit tab rendered them (site casing, site order)
    assert ats.keywords_matched[:3] == ["automation engineering", "automation", "automation tools"]
    assert ats.keywords_unmatched[:3] == ["automation methodologies", "project planning", "project execution"]
    assert (len(ats.keywords_matched), len(ats.keywords_unmatched)) == (44, 23)
    assert not {k.lower() for k in ats.keywords_matched} & {k.lower() for k in ats.keywords_unmatched}


def test_suggestion_from_site_advice_else_null():
    fb = parse_feedback(_html("gold_bronze"), "900101")
    by_name = {c.name: c for c in fb.categories.credibility.criteria}
    assert by_name["Experience Details"].suggestion.startswith("REDACTED")  # site improvement_advice (redacted)
    assert by_name["GPA"].suggestion is None  # site computes this one client-side


# --- synthetic edge cases: mutate a real fixture's props ---------------------------------------------------------

def _props(name="gold_bronze"):
    m = re.search(r'data-react-props="([^"]*)"', _html(name))
    return json.loads(html.unescape(m.group(1)))


def _embed(props):
    return f'<div data-react-class="{SUMMARY_APP}" data-react-props="{html.escape(json.dumps(props))}"></div>'


def _root_ids(props):
    return {i["id"] for i in props["resumeAiCriteriaScores"]["included"] if i["attributes"]["parent_id"] is None}


def test_medal_only_summary_is_partial():
    p = _props()
    roots = _root_ids(p)
    sc = p["resumeAiCriteriaScores"]
    sc["data"] = [d for d in sc["data"] if d["relationships"]["resume_ai_criterium"]["data"]["id"] in roots]
    fb = parse_feedback(_embed(p), "900101")
    assert fb.partial is True and fb.medal == "gold"
    assert fb.categories.ats_fit.badge == "silver" and fb.categories.ats_fit.criteria == []
    assert fb.action_items == []


def test_badge_null_edge_case():
    p = _props()
    ats_id = next(i["id"] for i in p["resumeAiCriteriaScores"]["included"] if i["attributes"]["slug"] == "ats_fit")
    for d in p["resumeAiCriteriaScores"]["data"]:
        if d["relationships"]["resume_ai_criterium"]["data"]["id"] == ats_id:
            d["attributes"]["score_type"] = None
    assert parse_feedback(_embed(p), "900101").categories.ats_fit.badge is None


def test_unknown_label_is_warning_never_throws():
    p = _props()
    d = next(d for d in p["resumeAiCriteriaScores"]["data"]
             if d["relationships"]["resume_ai_criterium"]["data"]["id"] not in _root_ids(p))
    d["attributes"]["score_type"] = "platinum"
    fb = parse_feedback(_embed(p), "900101")
    weird = [c for cat in ("readability", "credibility", "ats_fit", "format")
             for c in getattr(fb.categories, cat).criteria if "platinum" in c.detail]
    assert len(weird) == 1 and weird[0].status == "warning"


def test_incomplete_scan_is_invalid_input():
    p = _props()
    p["parsedResume"]["data"]["attributes"]["status"] = "processing"
    with pytest.raises(ToolError) as e:
        parse_feedback(_embed(p), "900101")
    assert e.value.code == "invalid_input"


def test_missing_props_raises_value_error():
    with pytest.raises(ValueError):
        parse_feedback("<html><body>nothing</body></html>", "x")


def test_ledger_hash_join():
    fb = parse_feedback(_html("gold_bronze"), "900101", {"resume_sha256": "sha256:abc"})
    assert fb.resume_sha256 == "sha256:abc"
    assert parse_feedback(_html("gold_bronze"), "900101").resume_sha256 is None


# --- get_scan_feedback flow ---------------------------------------------------------------------------------------

PORTAL = "https://portal.test/members/resume_assignments/review_summary/{}"


def _settings(tmp_path):
    return Settings(base_url="https://portal.test", data_dir=tmp_path, profile_dir=tmp_path / "profile", headless=True,
                    browser_channel=None, nav_timeout_ms=1000, human_delay_min_s=0, human_delay_max_s=0)


class FakePage:
    def __init__(self, page_html, url):
        self.html, self.url = page_html, url

    async def content(self):
        return self.html


@pytest.fixture
def fake_site(monkeypatch):
    def install(page_html, url):
        page = FakePage(page_html, url)

        async def new_page(settings):
            return page

        async def nothing(*a, **kw):
            pass

        monkeypatch.setattr(feedback, "new_page", new_page)
        monkeypatch.setattr(feedback, "require_login", nothing)
        monkeypatch.setattr(auth, "human_delay", nothing)
        monkeypatch.setattr(auth, "goto", nothing)
        return page
    return install


def _run(settings, scan_id):
    return asyncio.run(feedback.get_scan_feedback(settings, scan_id))


def _code(settings, scan_id):
    with pytest.raises(ToolError) as e:
        _run(settings, scan_id)
    return e.value.code


def test_get_scan_feedback_backs_up_and_snapshots(fake_site, tmp_path):
    fake_site(_html("gold_bronze"), PORTAL.format("900101"))
    fb = _run(_settings(tmp_path), "900101")
    backup = tmp_path / "feedback" / "900101.json"
    assert json.loads(backup.read_text())["medal"] == fb.medal == "gold"
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600
    assert list((tmp_path / "snapshots").glob("900101-feedback-*.html"))


@pytest.mark.parametrize("scan_id", ["", "abc", "12a", 675751, "²", "9" * 20])
def test_bad_scan_id_is_invalid_input(scan_id, tmp_path):
    assert _code(_settings(tmp_path), scan_id) == "invalid_input"


def test_redirect_away_is_not_found(fake_site, tmp_path):
    fake_site("<html>dashboard</html>", "https://portal.test/members/resume_dashboard")
    assert _code(_settings(tmp_path), "123") == "not_found"


def test_missing_props_is_site_changed(fake_site, tmp_path):
    fake_site("<html>new layout</html>", PORTAL.format("123"))
    assert _code(_settings(tmp_path), "123") == "site_changed"
    assert list((tmp_path / "snapshots").glob("123-feedback-*.html"))
    assert not (tmp_path / "feedback").exists()  # nothing backed up from an unreadable page


def test_page_for_other_scan_is_rejected():
    with pytest.raises(ValueError):
        parse_feedback(_html("gold_bronze"), "900102")


@pytest.mark.parametrize("url", [
    PORTAL.format("1234"),  # prefix of another id
    "https://portal.test/members/resume_dashboard?next=/members/resume_assignments/review_summary/123",
])
def test_lookalike_url_is_not_found(fake_site, tmp_path, url):
    fake_site(_html("gold_bronze"), url)
    assert _code(_settings(tmp_path), "123") == "not_found"
    assert not (tmp_path / "feedback").exists()


def test_backup_secures_data_dir_root(tmp_path):
    from resumeai_mcp.storage import backup_feedback
    old = os.umask(0o022)
    try:
        data_dir = tmp_path / "fresh" / "data"
        backup_feedback(_settings(data_dir), parse_feedback(_html("gold_bronze"), "900101"))
    finally:
        os.umask(old)
    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(data_dir / "feedback").st_mode) == 0o700


def test_no_category_badges_is_site_changed_not_partial():
    p = _props()
    p["resumeAiCriteriaScores"]["data"] = []
    with pytest.raises(ValueError):
        parse_feedback(_embed(p), "900101")


def test_action_item_keeps_the_actual_advice():
    fb = parse_feedback(_html("gold_bronze"), "900101")
    job_title = next(a for a in fb.action_items if a.item.startswith("Job Title Match"))
    assert "Consider using the phrase" in job_title.item


def test_snapshot_write_failure_is_storage_error(fake_site, tmp_path):
    fake_site(_html("gold_bronze"), PORTAL.format("900101"))
    (tmp_path / "snapshots").write_text("not a directory")  # makes the snapshot write fail with OSError
    assert _code(_settings(tmp_path), "900101") == "storage_error"
    assert not (tmp_path / "feedback").exists()  # no backup claimed after a failed snapshot


def test_snapshot_failure_on_site_changed_path_is_storage_error(fake_site, tmp_path):
    fake_site("<html>new layout</html>", PORTAL.format("123"))
    (tmp_path / "snapshots").write_text("not a directory")
    assert _code(_settings(tmp_path), "123") == "storage_error"
