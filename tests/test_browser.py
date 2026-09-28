import os
import socket
import subprocess

from resumeai_mcp.browser import profile_in_use


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
