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

Login, Claude Code config, and troubleshooting are documented as each phase lands (full README in Phase 6).
