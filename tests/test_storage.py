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


def test_ledger_append_update_and_attempt_keys(tmp_path):
    from resumeai_mcp.storage import append_ledger, load_ledger, update_ledger
    s = _settings(tmp_path / "data")
    old = os.umask(0o022)
    try:
        append_ledger(s, {"scan_id": None, "attempt_id": "a1", "resume_filename": "r.pdf"})
        append_ledger(s, {"scan_id": "7", "medal": None})
        update_ledger(s, "7", medal="gold")
    finally:
        os.umask(old)
    ledger = load_ledger(s)
    assert set(ledger) == {"attempt:a1", "7"} and ledger["7"]["medal"] == "gold"
    assert _mode(s.data_dir / "ledger.jsonl") == 0o600 and _mode(s.data_dir) == 0o700
    assert len((s.data_dir / "ledger.jsonl").read_text().splitlines()) == 3  # append-only


def test_existing_permissive_ledger_is_tightened(tmp_path):
    from resumeai_mcp.storage import append_ledger
    s = _settings(tmp_path / "data")
    s.data_dir.mkdir(parents=True)
    ledger = s.data_dir / "ledger.jsonl"
    ledger.write_text("")
    ledger.chmod(0o644)
    append_ledger(s, {"scan_id": "1"})
    assert _mode(ledger) == 0o600


def test_snapshots_older_than_30_days_are_purged(tmp_path):
    import time
    from resumeai_mcp.storage import save_snapshot
    s = _settings(tmp_path / "data")
    old = save_snapshot(s, "old", "<html>old</html>")
    stale = time.time() - 31 * 86400
    os.utime(old, (stale, stale))
    recent = save_snapshot(s, "new", "<html>new</html>")  # saving triggers the §14 purge
    assert not old.exists() and recent.exists()
