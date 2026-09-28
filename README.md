# resumeai-mcp

Local MCP server (stdio) that lets a Claude Code agent drive Big Interview ResumeAI. Spec: [PLAN.md](PLAN.md).

## Setup

```bash
uv sync
uv run playwright install chromium
cp .env.example .env
```

Login, Claude Code config, and troubleshooting are documented as each phase lands (full README in Phase 6).
