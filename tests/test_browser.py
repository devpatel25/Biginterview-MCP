import asyncio
import os
import socket
import subprocess

from resumeai_mcp import browser
from resumeai_mcp.browser import profile_in_use
from resumeai_mcp.config import Settings


def _lock(tmp_path, target):
    os.symlink(target, tmp_path / "SingletonLock")


def test_no_lock(tmp_path):
    assert not profile_in_use(tmp_path)


def test_live_pid_locked(tmp_path):
    _lock(tmp_path, f"{socket.gethostname()}-{os.getpid()}")
    assert profile_in_use(tmp_path)


def test_dead_pid_stale(tmp_path):
    proc = subprocess.Popen(["true"])
    proc.wait()
    _lock(tmp_path, f"{socket.gethostname()}-{proc.pid}")
    assert not profile_in_use(tmp_path)


def test_other_host_locked(tmp_path):
    _lock(tmp_path, f"some-other-host-{os.getpid()}")
    assert profile_in_use(tmp_path)


def test_garbage_lock_locked(tmp_path):
    _lock(tmp_path, "not-a-pid")
    assert profile_in_use(tmp_path)


def test_out_of_range_pid_locked(tmp_path):
    _lock(tmp_path, f"{socket.gethostname()}-99999999999999999999")
    assert profile_in_use(tmp_path)


def test_nonpositive_pid_locked(tmp_path):
    _lock(tmp_path, f"{socket.gethostname()}-0")
    assert profile_in_use(tmp_path)


def test_concurrent_ensure_context_launches_once(tmp_path, monkeypatch):
    launches = []

    class FakeCtx:
        pages = []

        def set_default_timeout(self, ms):
            pass

        async def close(self):
            pass

    class FakeChromium:
        async def launch_persistent_context(self, **kw):
            launches.append(kw)
            await asyncio.sleep(0.01)  # yield so the second caller races the first
            return FakeCtx()

    class FakePW:
        chromium = FakeChromium()

        async def stop(self):
            pass

    class FakeStarter:
        async def start(self):
            return FakePW()

    monkeypatch.setattr(browser, "async_playwright", FakeStarter)
    settings = Settings(
        base_url="https://example.test", data_dir=tmp_path, profile_dir=tmp_path / "profile",
        headless=True, browser_channel=None, nav_timeout_ms=1000,
        human_delay_min_s=0, human_delay_max_s=0,
    )

    async def run():
        try:
            return await asyncio.gather(browser.ensure_context(settings), browser.ensure_context(settings))
        finally:
            await browser.close_context()

    a, b = asyncio.run(run())
    assert a is b
    assert len(launches) == 1
