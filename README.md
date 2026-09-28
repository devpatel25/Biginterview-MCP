# resumeai-mcp

Local MCP server (stdio) that lets a Claude Code agent drive Big Interview ResumeAI. Spec: [PLAN.md](PLAN.md).

## Setup

```bash
uv sync
cp .env.example .env
```

The default `BROWSER_CHANNEL=chrome` uses your installed Google Chrome (PLAN.md §6.3). Install it
if missing (`uv run playwright install chrome`), or use bundled Chromium instead:
`uv run playwright install chromium` and set `BROWSER_CHANNEL=` (empty) in `.env`.

## Add to Claude Code

```bash
claude mcp add --scope user resumeai -- uv --directory /absolute/path/to/Biginterview-MCP run resumeai-mcp
claude mcp get resumeai   # Status: ✔ Connected
```

The server exposes six tools (`auth_status`, `list_scans`, `start_scan`, `get_scan_status`, `get_scan_feedback`,
`delete_scan`); every call returns `{"ok": true, "data": …}` or `{"ok": false, "error": {code, message, hint}}`
(PLAN.md §7.0, §13). Log in first with `uv run python scripts/login.py`, and never run it while the server is
using the browser profile.

Troubleshooting and the full walkthrough land in Phase 6.
