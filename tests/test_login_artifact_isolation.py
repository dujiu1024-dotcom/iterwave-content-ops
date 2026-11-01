"""登录产物（二维码 / 状态 / 短信校验截图 / 浏览器 Profile / storage_state）的账号组隔离与权限。

vendor/easel 保持上游原样：隔离靠外层 account_login 传入账号组目录（--profile-base /
--qr-out / --status-file），权限靠外层预建 0700 目录 + 子进程 umask 077。
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from src.core import account_login as al

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")


@pytest.fixture()
def default_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "LOGIN_DIR", tmp_path / "default" / "_login")
    monkeypatch.setattr(al, "PROFILE_BASE", tmp_path / "default" / "_profiles")
    monkeypatch.setattr(al, "STORAGE_STATE_DIR", tmp_path / "default" / "login_states")
    monkeypatch.delenv("ITERWAVE_LOGIN_HEADED", raising=False)
    al._LOGIN_PROCESSES.clear()
    with al._EXPORT_LOCK:
        al._EXPORTING.clear()
    yield tmp_path
    al._LOGIN_PROCESSES.clear()


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


@pytest.mark.parametrize("platform", al.PLATFORMS)
def test_login_cmd_artifacts_follow_tenant_data_dir(default_dirs, platform):
    tenant_a = default_dirs / "tenants" / "a" / "data"
    tenant_b = default_dirs / "tenants" / "b" / "data"

    cmd_a = al.build_cmd(platform, timeout_seconds=60, data_dir=tenant_a)
    cmd_b = al.build_cmd(platform, timeout_seconds=60, data_dir=tenant_b)

    for cmd, root in ((cmd_a, tenant_a.resolve()), (cmd_b, tenant_b.resolve())):
        assert _arg(cmd, "--profile-base") == str(root / "_profiles")
        assert _arg(cmd, "--qr-out") == str(root / "_login" / f"{platform}.png")
        assert _arg(cmd, "--status-file") == str(root / "_login" / f"{platform}.json")
        if platform == "douyin":
            # 抖音短信校验 DOM/截图写在 qr_out 同目录，因此同样落在账号组 _login 下
            assert _arg(cmd, "--sms-code-file") == str(root / "_login" / "douyin.code")
    assert _arg(cmd_a, "--qr-out") != _arg(cmd_b, "--qr-out")


@POSIX_ONLY
def test_start_login_prepares_private_tenant_dirs_and_umask(default_dirs, monkeypatch):
    tenant = default_dirs / "tenants" / "a" / "data"
    captured = {}

    class FakeProc:
        returncode = None

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(al.subprocess, "Popen", fake_popen)
    al.start_login("xhs", data_dir=tenant)

    root = tenant.resolve()
    assert captured["kwargs"]["umask"] == 0o077
    for d in (root / "_login", root / "_profiles", root / "login_states",
              root / "_profiles" / al.PROFILE_NAMES["xhs"]):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    assert stat.S_IMODE((root / "_login" / "xhs.log").stat().st_mode) == 0o600
    # 默认账号组目录不应被非默认账号组的登录触碰
    assert not al.LOGIN_DIR.exists()
    assert not al.PROFILE_BASE.exists()


@POSIX_ONLY
def test_existing_profile_dir_is_tightened(default_dirs):
    tenant = default_dirs / "tenants" / "a" / "data"
    profile = al.profile_dir("kuaishou", tenant)
    profile.mkdir(parents=True)
    profile.chmod(0o755)

    al._ensure_profile_dir("kuaishou", tenant)

    assert stat.S_IMODE(profile.stat().st_mode) == 0o700


@POSIX_ONLY
def test_private_umask_makes_child_written_files_private(tmp_path):
    target = tmp_path / "qr.png"
    script = (
        "import pathlib, sys; p = pathlib.Path(sys.argv[1]); "
        "p.write_bytes(b'x'); (p.parent / 'profile').mkdir()"
    )
    subprocess.run([sys.executable, "-c", script, str(target)], check=True,
                   **al._private_subprocess_kwargs())

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "profile").stat().st_mode) == 0o700


def _run_threads_inline(monkeypatch):
    class InlineThread:
        def __init__(self, target, name=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(al.threading, "Thread", InlineThread)


def _capture_export_run(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return Result()

    monkeypatch.setattr(al.subprocess, "run", fake_run)
    return calls


def test_schedule_export_for_tenant_writes_into_tenant_not_default(default_dirs, monkeypatch):
    tenant = default_dirs / "tenants" / "a" / "data"
    al._ensure_dirs(tenant)
    al._files("xhs", tenant)["status"].write_text(json.dumps({"state": "success"}), encoding="utf-8")
    _run_threads_inline(monkeypatch)
    calls = _capture_export_run(monkeypatch)

    al._schedule_export("xhs", data_dir=tenant)

    assert len(calls) == 1
    cmd = calls[0]["cmd"]
    root = tenant.resolve()
    assert _arg(cmd, "--profile-base") == str(root / "_profiles")
    assert _arg(cmd, "--out") == str(root / "login_states" / "xhs.json")
    assert str(al.STORAGE_STATE_DIR) not in _arg(cmd, "--out")
    if os.name != "nt":
        assert calls[0]["kwargs"]["umask"] == 0o077


def test_stop_tenant_logins_exports_completed_login_to_same_tenant(default_dirs, monkeypatch):
    tenant = default_dirs / "tenants" / "b" / "data"
    al._ensure_dirs(tenant)
    al._files("douyin", tenant)["status"].write_text(json.dumps({"state": "success"}), encoding="utf-8")

    class DoneProc:
        returncode = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    al._LOGIN_PROCESSES[al._process_key("douyin", tenant)] = DoneProc()
    _run_threads_inline(monkeypatch)
    calls = _capture_export_run(monkeypatch)

    al.stop_tenant_logins(tenant, timeout_seconds=2)

    assert len(calls) == 1
    root = tenant.resolve()
    assert _arg(calls[0]["cmd"], "--out") == str(root / "login_states" / "douyin.json")
    assert _arg(calls[0]["cmd"], "--profile-base") == str(root / "_profiles")
    assert not al.STORAGE_STATE_DIR.exists()
