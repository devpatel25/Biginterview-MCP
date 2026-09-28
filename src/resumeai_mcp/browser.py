"""Playwright persistent-context lifecycle, human_delay, snapshots (PLAN.md §6, §12)."""

import asyncio
import os
import random
import socket
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from . import storage
from .config import Settings, private_dir


class ProfileInUseError(RuntimeError):
    """Another browser (e.g. login.py) holds the persistent profile (§6.1)."""


def profile_in_use(profile_dir: Path) -> bool:
    """True if a live Chrome process holds `profile_dir`.

    Chrome marks an open profile with a `SingletonLock` symlink pointing at
    "<hostname>-<pid>". A lock whose pid is dead is stale and Chrome clears it itself.
    """
    lock = profile_dir / "SingletonLock"
    if not os.path.lexists(lock):
        return False
    try:
        host, _, pid = os.readlink(lock).rpartition("-")
        pid_num = int(pid)
    except (OSError, ValueError):
        return True  # unreadable lock: assume held rather than risk corrupting the profile
    if host != socket.gethostname():
        return True  # can't check a pid on another host
    try:
        os.kill(pid_num, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_pw: Playwright | None = None
_ctx: BrowserContext | None = None


async def ensure_context(settings: Settings, headless: bool | None = None) -> BrowserContext:
    """Return the shared persistent context, launching it on first use."""
    global _pw, _ctx
    if _ctx is not None:
        return _ctx
    profile = private_dir(settings.profile_dir)
    if profile_in_use(profile):
        raise ProfileInUseError(
            f"Browser profile {profile} is in use (is scripts/login.py running?). Close it and retry."
        )
    _pw = await async_playwright().start()
    try:
        _ctx = await _pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=settings.headless if headless is None else headless,
            channel=settings.browser_channel,
        )
    except BaseException:
        await _pw.stop()
        _pw = None
        raise
    _ctx.set_default_timeout(settings.nav_timeout_ms)
    return _ctx


async def close_context() -> None:
    global _pw, _ctx
    if _ctx is not None:
        await _ctx.close()
        _ctx = None
    if _pw is not None:
        await _pw.stop()
        _pw = None


async def new_page(settings: Settings, headless: bool | None = None) -> Page:
    ctx = await ensure_context(settings, headless)
    return ctx.pages[0] if ctx.pages else await ctx.new_page()


async def goto(page: Page, settings: Settings, path: str) -> None:
    await page.goto(settings.base_url + path, wait_until="domcontentloaded")


async def human_delay(settings: Settings) -> None:
    """Random pause between UI actions (§12: 2–5 s by default)."""
    await asyncio.sleep(random.uniform(settings.human_delay_min_s, settings.human_delay_max_s))


async def snapshot(page: Page, settings: Settings, name: str) -> Path:
    return storage.save_snapshot(settings, name, await page.content())
