"""One-time headed login into the persistent profile (PLAN.md §6.1).

    uv run python scripts/login.py          # headed: log in via SSO + Duo, press ENTER
    uv run python scripts/login.py --check  # headless: verify the saved session is reused

Never run this while the MCP server is running (shared profile directory).
"""

import argparse
import asyncio
import sys

from resumeai_mcp.auth import LOGGED_IN, SCAN_PATH, login_state
from resumeai_mcp.browser import close_context, goto, new_page, snapshot
from resumeai_mcp.config import load_settings


async def main(check: bool) -> bool:
    settings = load_settings()
    page = await new_page(settings, headless=check)
    try:
        if not check:
            await goto(page, settings, SCAN_PATH)
            await asyncio.to_thread(input, "Log in via SSO + Duo, then press ENTER here.")
        state = await login_state(page, settings)
        ok = state == LOGGED_IN
        print(f"logged_in={str(ok).lower()}" + ("" if ok else f" ({state})"))
        if not ok:
            print(f"snapshot: {await snapshot(page, settings, 'login-check')}")
        return ok
    finally:
        await close_context()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="headless session check, no login prompt")
    sys.exit(0 if asyncio.run(main(parser.parse_args().check)) else 1)
