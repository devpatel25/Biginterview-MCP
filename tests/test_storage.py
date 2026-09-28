import os
import stat

from resumeai_mcp.config import Settings
from resumeai_mcp.storage import save_snapshot


def _settings(data_dir):
    return Settings(
        base_url="https://portal.test", data_dir=data_dir, profile_dir=data_dir / "profile",
        headless=True, browser_channel=None, nav_timeout_ms=1000, human_delay_min_s=0, human_delay_max_s=0,
    )


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_snapshot_permissions_under_permissive_umask(tmp_path):
    old = os.umask(0o022)
    try:
        data_dir = tmp_path / "a" / "data"
        path = save_snapshot(_settings(data_dir), "x", "<html>pii</html>")
    finally:
        os.umask(old)
    assert _mode(data_dir) == 0o700
    assert _mode(data_dir / "snapshots") == 0o700
    assert _mode(path) == 0o600
    assert path.read_text(encoding="utf-8") == "<html>pii</html>"


def test_same_second_snapshots_do_not_collide(tmp_path):
    s = _settings(tmp_path / "data")
    a, b = save_snapshot(s, "x", "one"), save_snapshot(s, "x", "two")
    assert a != b
    assert (a.read_text(), b.read_text()) == ("one", "two")
