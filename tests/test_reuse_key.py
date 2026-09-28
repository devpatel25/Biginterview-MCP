"""Reuse key, JD normalization, input validation, status mapping, id capture (§7.3, §10.1, §10.2)."""

import io
import zipfile
import zlib
from datetime import datetime, timedelta, timezone

import pytest

from resumeai_mcp.scans import (displayed_name_matches, entry_key, find_new_row, has_extractable_text, jd_sha256, normalize_jd, reuse_key,
                                row_state, strip_boilerplate, validate_inputs)
from resumeai_mcp.schemas import ToolError


def _pdf(content: bytes, compress: bool = False) -> bytes:
    body = zlib.compress(content) if compress else content
    extra = b" /Filter /FlateDecode" if compress else b""
    return (b"%%PDF-1.4\n1 0 obj\n<< /Length %d%s >>\nstream\n" % (len(body), extra) + body
            + b"\nendstream\nendobj\n%%EOF\n")


TEXT_OPS = b"BT /F1 11 Tf 50 750 Td (Jane Doe - Software Engineer) Tj ET"
IMAGE_OPS = b"q 612 0 0 792 0 0 cm /Im0 Do Q"


def test_jd_normalization_is_case_and_whitespace_insensitive():
    assert normalize_jd("  Build  APIs\n\nin Python ") == "build apis in python"
    assert jd_sha256("Build APIs\tin Python") == jd_sha256("build apis in   python")
    assert jd_sha256("Build APIs in Python") != jd_sha256("Build APIs in Go")


def test_reuse_key_is_the_full_tuple():
    base = ("sha256:r", "sha256:j", "Graduate - STEM Focus", "AI Intern", "Welldoc")
    k = reuse_key(*base)
    assert k == reuse_key("sha256:r", "sha256:j", " graduate - stem focus", "ai intern ", "WELLDOC")
    for i in range(5):  # §10.1: any single field differing breaks reuse
        changed = list(base)
        changed[i] = changed[i] + "x"
        assert reuse_key(*changed) != k


def test_site_created_and_pending_entries_never_reusable():
    assert entry_key({"scan_id": "1", "resume_sha256": None, "jd_sha256": None}) is None
    assert entry_key({"scan_id": None, "attempt_id": "a", "resume_sha256": "x", "jd_sha256": "y"}) is None
    full = {"scan_id": "1", "resume_sha256": "r", "jd_sha256": "j", "scoring_guide": "G", "role_title": "T",
            "company": "C"}
    assert entry_key(full) == reuse_key("r", "j", "G", "T", "C")


def test_strip_boilerplate_keeps_substance():
    jd = ("Build ML pipelines in Python.\n\nWe are an Equal Opportunity Employer.\n\n"
          "Requirements: PyTorch, SQL.\n\nBenefits: 401(k), health insurance, PTO.")
    out = strip_boilerplate(jd)
    assert "Python" in out and "PyTorch" in out
    assert "Equal Opportunity" not in out and "401" not in out
    assert strip_boilerplate("We are an equal opportunity employer.") == "We are an equal opportunity employer."


def test_extractable_text(tmp_path):
    cases = {"plain.pdf": _pdf(TEXT_OPS), "flate.pdf": _pdf(TEXT_OPS, compress=True)}
    for name, data in cases.items():
        (tmp_path / name).write_bytes(data)
        assert has_extractable_text(tmp_path / name), name
    (tmp_path / "image.pdf").write_bytes(_pdf(IMAGE_OPS, compress=True))
    assert not has_extractable_text(tmp_path / "image.pdf")
    assert has_extractable_text(_docx(tmp_path, "ok.docx", "<w:p><w:r><w:t>Jane Doe</w:t></w:r></w:p>"))
    (tmp_path / "broken.docx").write_bytes(b"not a zip")
    assert not has_extractable_text(tmp_path / "broken.docx")


def _args(path, **over):
    args = {"resume_path": str(path), "job_title": "AI Intern", "company": "Acme", "job_description": "Build things",
            "scoring_guide": "Graduate - STEM Focus"}
    return {**args, **over}


@pytest.mark.parametrize("over,setup", [
    ({"job_title": ""}, "pdf"), ({"company": "  "}, "pdf"), ({"job_description": None}, "pdf"),
    ({"resume_path": "relative/resume.pdf"}, None), ({}, "missing"), ({}, "txt"), ({}, "big"), ({}, "image"),
])
def test_validate_inputs_rejects(tmp_path, over, setup):
    path = tmp_path / ("resume.txt" if setup == "txt" else "resume.pdf")
    if setup in ("pdf", "txt"):
        path.write_bytes(_pdf(TEXT_OPS))
    elif setup == "big":
        path.write_bytes(_pdf(TEXT_OPS) + b"0" * (5 * 1024 * 1024))
    elif setup == "image":
        path.write_bytes(_pdf(IMAGE_OPS))
    with pytest.raises(ToolError) as e:
        validate_inputs(**_args(path, **over))
    assert e.value.code == "invalid_input"


def test_validate_inputs_accepts(tmp_path):
    path = tmp_path / "Resume (1).pdf"
    path.write_bytes(_pdf(TEXT_OPS))
    assert validate_inputs(**_args(path)) == path


def test_row_state_never_maps_unknown_to_failed():
    assert row_state({"status": "success"}) == "complete"
    assert row_state({"status": "failed"}) == "failed"
    assert row_state({"status": "processing"}) == "scanning"
    assert row_state({"status": "something_new"}) == "unknown"
    assert row_state({}) == "unknown"


def _row(i, name, title, created):
    return {"id": str(i), "attributes": {"document_file_name": name, "job_title": title,
                                         "created_at": created.isoformat().replace("+00:00", "Z")}}


def test_find_new_row_matches_site_normalized_filename_title_and_time():
    t0 = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
    rows = [_row(3, "Resume_(1).pdf", "AI Intern", t0 + timedelta(seconds=5)),
            _row(2, "Resume_(1).pdf", "AI Intern", t0 - timedelta(hours=1))]
    assert find_new_row(rows, "Resume (1).pdf", "ai intern", t0, set())["id"] == "3"
    assert find_new_row(rows[1:], "Resume (1).pdf", "AI Intern", t0, set()) is None  # too old
    assert find_new_row(rows, "Other.pdf", "AI Intern", t0, set()) is None
    assert find_new_row(rows, "Resume (1).pdf", "SWE", t0, set()) is None


def test_find_new_row_never_captures_a_row_listed_before_the_click():
    t0 = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
    old = _row(2, "resume.pdf", "AI Intern", t0 - timedelta(seconds=20))  # same file+title, 20 s before click
    assert find_new_row([old], "resume.pdf", "AI Intern", t0, {"2"}) is None
    new = _row(3, "resume.pdf", "AI Intern", t0 + timedelta(seconds=10))
    assert find_new_row([new, old], "resume.pdf", "AI Intern", t0, {"2"})["id"] == "3"


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(tmp_path, name, body):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
    (tmp_path / name).write_bytes(buf.getvalue())
    return tmp_path / name


@pytest.mark.parametrize("ops,expected", [
    (b"BT /F1 11 Tf <48656C6C6F20576F726C64> Tj ET", True),   # hex string (pdftotext: "Hello World")
    (b"BT /F1 11 Tf [(Hel) -20 (lo)] TJ ET", True),            # TJ array
    (b"BT /F1 11 Tf [<0048> 12 <0065>] TJ ET", True),          # CID-font hex array
    (b"BT /F1 11 Tf (Line two) ' ET", True),                   # ' operator
    (b"BT /F1 11 Tf () Tj ET", False),                          # empty string shows nothing
    (b"BT /F1 11 Tf <> Tj ET", False),
    (b"BT /F1 11 Tf (   ) Tj ET", False),                       # whitespace-only literal
    (b"BT /F1 11 Tf <202020> Tj ET", False),                    # whitespace-only hex
    (b"BT /F1 11 Tf [(  ) -20 <20>] TJ ET", False),
    (b"BT /F1 11 Tf [(  ) -20 (Hi)] TJ ET", True),
    (rb"BT /F1 11 Tf (\040\040) Tj ET", False),                # escaped spaces (octal)
    (rb"BT /F1 11 Tf (\t\n) Tj ET", False),                     # escaped whitespace
    (b"BT /F1 11 Tf <2> Tj ET", False),                          # odd hex pads to <20> = space
    (rb"BT /F1 11 Tf (\101) Tj ET", True),                      # octal 101 = "A"
    (b"BT /F1 11 Tf <4> Tj ET", True),                           # pads to <40> = "@"
    (IMAGE_OPS, False),
])
def test_pdf_text_operators(tmp_path, ops, expected):
    for compress in (False, True):
        p = tmp_path / f"t{int(compress)}.pdf"
        p.write_bytes(_pdf(ops, compress))
        assert has_extractable_text(p) is expected, (ops, compress)


def test_docx_text_nodes_not_attributes(tmp_path):
    empty = _docx(tmp_path, "empty.docx", '<w:p><w:r><w:t xml:space="preserve">  </w:t></w:r></w:p>')
    assert not has_extractable_text(empty)
    full = _docx(tmp_path, "full.docx", '<w:p><w:r><w:t xml:space="preserve">Jane Doe</w:t></w:r></w:p>')
    assert has_extractable_text(full)


def test_displayed_filename_check_handles_site_truncation():
    name = "2026-09-26__Resume_InternDataScientist_OneCommunityGlobalInc.pdf"
    assert displayed_name_matches("2026-09-26__Resume_InternDataScientis...", name)  # as rendered live
    assert displayed_name_matches(name, name)
    assert displayed_name_matches("Resume_Int…", "Resume_Intern.pdf")
    assert not displayed_name_matches("2026-09-25__Resume_Other...", name)
    assert not displayed_name_matches("other.pdf", name)
    assert not displayed_name_matches("...", name)
