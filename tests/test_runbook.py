"""RUNBOOK.md must carry PLAN.md §11's honesty rule verbatim and every §13 error code."""

import re
from pathlib import Path

from resumeai_mcp.schemas import ErrorCode

ROOT = Path(__file__).parent.parent


def _quote_block(text: str, starts: str) -> str:
    block = re.search(r"((?:^> .*\n?)+)", text[text.index(starts):], re.M).group(1)
    return " ".join(block.replace("> ", "").split())


def test_honesty_rule_verbatim():
    plan, runbook = (ROOT / "PLAN.md").read_text(), (ROOT / "RUNBOOK.md").read_text()
    assert _quote_block(runbook, "## Honesty rule") == _quote_block(plan, "**Honesty rule")


def test_every_error_code_has_an_action():
    runbook = (ROOT / "RUNBOOK.md").read_text()
    for code in ErrorCode.__args__:
        assert f"| `{code}` |" in runbook, code
