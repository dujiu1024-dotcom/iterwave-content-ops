from __future__ import annotations

import asyncio
from pathlib import Path

from src.adapters.base import Platform
from src.core import login_manager


def test_tenant_login_dir_is_scoped_and_restored(tmp_path):
    original = login_manager.LOGIN_DIR
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"
    try:
        with login_manager.tenant_login_dir(tenant_a):
            assert login_manager._platform_state_path(Platform.XHS) == tenant_a / "login_states" / "xhs.json"
        assert login_manager.LOGIN_DIR == original
        assert login_manager._platform_state_path(Platform.XHS) == original / "xhs.json"
    finally:
        login_manager.LOGIN_DIR = original


def test_async_tasks_keep_distinct_tenant_login_dirs(tmp_path):
    async def run():
        async def inspect(directory: Path):
            with login_manager.tenant_login_dir(directory):
                await asyncio.sleep(0)
                return login_manager._platform_state_path(Platform.DOUYIN)

        a, b = await asyncio.gather(
            inspect(tmp_path / "tenant-a"),
            inspect(tmp_path / "tenant-b"),
        )
        assert a == tmp_path / "tenant-a" / "login_states" / "douyin.json"
        assert b == tmp_path / "tenant-b" / "login_states" / "douyin.json"

    asyncio.run(run())


def test_explicit_tenant_login_state_path_overrides_global_and_context(tmp_path):
    tenant = tmp_path / "tenant-c"
    expected = tenant / "login_states" / "xhs.json"
    with login_manager.tenant_login_dir(tmp_path / "tenant-a"):
        assert login_manager._platform_state_path(Platform.XHS, data_dir=tenant) == expected
        assert login_manager.has_login_state(Platform.XHS, data_dir=tenant) is False
        expected.parent.mkdir(parents=True)
        expected.write_text("{}", encoding="utf-8")
        assert login_manager.has_login_state(Platform.XHS, data_dir=tenant) is True


def test_async_context_state_is_atomically_written_back_to_tenant(tmp_path):
    async def run():
        tenant = tmp_path / "tenant-state"
        state_path = tenant / "login_states" / "xhs.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text('{"cookies":[{"name":"old"}],"origins":[]}', encoding="utf-8")

        class FakeAsyncContext:
            async def storage_state(self, path=None):
                Path(path).write_text('{"cookies":[{"name":"refreshed"}],"origins":[]}', encoding="utf-8")

        with login_manager.tenant_login_dir(tenant):
            saved = await login_manager.save_context_state_async(FakeAsyncContext(), Platform.XHS)
            assert saved == state_path
            assert '"refreshed"' in state_path.read_text(encoding="utf-8")
            assert not list(state_path.parent.glob("*.tmp"))

    asyncio.run(run())


def test_async_context_does_not_create_state_for_unlogged_platform(tmp_path):
    async def run():
        class FakeAsyncContext:
            async def storage_state(self, path=None):
                raise AssertionError("must not persist a context without prior login state")

        with login_manager.tenant_login_dir(tmp_path / "empty-tenant"):
            assert await login_manager.save_context_state_async(FakeAsyncContext(), Platform.DOUYIN) is None

    asyncio.run(run())
