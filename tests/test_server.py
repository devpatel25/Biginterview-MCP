"""§7.0 envelope and tool surface via the in-memory FastMCP client (§16 MCP contract, offline)."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastmcp import Client

from resumeai_mcp import auth, feedback, scans, server
from resumeai_mcp.schemas import AuthStatus, ToolError

TOOLS = {"auth_status", "list_scans", "start_scan", "get_scan_status", "get_scan_feedback", "delete_scan"}


def _call(name, args=None):
    async def go():
        async with Client(server.mcp) as c:
            r = await c.call_tool(name, args or {}, raise_on_error=False)
            assert not r.is_error  # failures travel inside the envelope, never as MCP errors
            return r.structured_content
    return asyncio.run(go())


def _assert_envelope(body):
    assert set(body) in ({"ok", "data"}, {"ok", "error"})
    if not body["ok"]:
        assert set(body["error"]) == {"code", "message", "hint"}


def test_exactly_six_tools_with_contract_descriptions():
    async def go():
        async with Client(server.mcp) as c:
            return {t.name: t for t in await c.list_tools()}
    tools = asyncio.run(go())
    assert set(tools) == TOOLS
    for t in tools.values():
        assert "2-5 s" in t.description and "PLAN.md §13" in t.description  # pacing + error notes (§7)
    assert "Never fabricate" in tools["get_scan_feedback"].description  # §7.5 honesty rule
    assert set(tools["start_scan"].inputSchema["properties"]) == {
        "resume_path", "job_title", "company", "job_description", "scoring_guide"}


def test_success_envelope(monkeypatch):
    async def fake(settings):
        return AuthStatus(logged_in=True, scans_remaining=4, account_email=None,
                          checked_at=datetime(2026, 9, 28, tzinfo=timezone.utc))
    monkeypatch.setattr(auth, "auth_status", fake)
    body = _call("auth_status")
    _assert_envelope(body)
    assert body == {"ok": True, "data": {"logged_in": True, "scans_remaining": 4, "account_email": None,
                                         "checked_at": "2026-09-28T00:00:00Z"}}


def test_tool_error_envelope(monkeypatch):
    async def fake(settings, scan_id):
        raise ToolError("auth_expired", "Session expired.", "Run scripts/login.py.")
    monkeypatch.setattr(feedback, "get_scan_feedback", fake)
    body = _call("get_scan_feedback", {"scan_id": "1"})
    _assert_envelope(body)
    assert body["error"] == {"code": "auth_expired", "message": "Session expired.", "hint": "Run scripts/login.py."}


def test_unexpected_exception_is_internal_error_without_details(monkeypatch):
    async def fake(settings, scan_id):
        raise RuntimeError("Executable doesn't exist at /Users/someone/Library/ms-playwright/chrome")
    monkeypatch.setattr(scans, "get_scan_status", fake)
    body = _call("get_scan_status", {"scan_id": "1"})
    _assert_envelope(body)
    assert body["error"]["code"] == "internal_error"
    assert "someone" not in body["error"]["message"] and "RuntimeError" in body["error"]["message"]


@pytest.mark.parametrize("name,args,field", [
    ("list_scans", {"limit": "abc"}, "limit"),
    ("delete_scan", {"scan_id": "1", "confirm": "maybe"}, "confirm"),
    ("start_scan", {"resume_path": "/x.pdf"}, "job_title"),
])
def test_bad_argument_types_stay_in_envelope(name, args, field):
    body = _call(name, args)
    _assert_envelope(body)
    assert body["error"]["code"] == "invalid_input" and field in body["error"]["message"]


@pytest.mark.parametrize("fail", [False, True])
def test_lifespan_releases_browser_profile(monkeypatch, fail):
    """Our lifespan's cleanup on normal and exceptional exit. (Driven directly: FastMCP's in-memory transport may
    cancel the server task before lifespan teardown, which would make an end-to-end assertion flaky.)"""
    closed = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(server, "close_context", fake_close)

    async def go():
        async with server.lifespan(server.mcp):
            if fail:
                raise RuntimeError("session crashed")

    if fail:
        with pytest.raises(RuntimeError):
            asyncio.run(go())
    else:
        asyncio.run(go())
    assert closed  # scripts/login.py can use the profile afterwards
    assert server.mcp._lifespan is server.lifespan  # and it is the lifespan the app runs with
