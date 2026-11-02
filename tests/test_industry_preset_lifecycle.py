"""P1-06 industry preset lifecycle and immutable version tests."""
from src.core.industry_presets_repository import (
    delete_custom_preset,
    get_preset,
    list_preset_versions,
    restore_custom_preset,
    seed_builtins,
    upsert_custom_preset,
)


def _preset(key="custom-p1"):
    return {
        "key": key, "name": "自定义模板", "description": "测试",
        "positioning": "帮助目标用户解决实际问题",
        "persona_keywords": ["经验"], "forbidden_directions": [],
        "extra_redlines": [], "dimensions": ["方法"],
        "platform_notes": {"xhs": "注明来源"},
    }


def test_industry_preset_update_archives_old_snapshot_and_restore_is_soft(conn):
    seed_builtins(conn)
    first = upsert_custom_preset(conn, _preset())
    assert first["version"] == 1
    updated = upsert_custom_preset(conn, {**_preset(), "name": "更新后的模板"})
    assert updated["version"] == 2
    versions = list_preset_versions(conn, "custom-p1")
    assert versions[0]["version"] == 1
    assert versions[0]["snapshot"]["name"] == "自定义模板"

    assert delete_custom_preset(conn, "custom-p1") is True
    archived = get_preset(conn, "custom-p1")
    assert archived["is_active"] is False
    assert archived["version"] == 3
    assert restore_custom_preset(conn, "custom-p1")["is_active"] is True
    assert get_preset(conn, "custom-p1")["version"] == 4
    assert len(list_preset_versions(conn, "custom-p1")) == 2


def test_industry_preset_builtin_cannot_be_archived_or_overwritten(conn):
    seed_builtins(conn)
    import pytest
    with pytest.raises(ValueError):
        delete_custom_preset(conn, "general")
    with pytest.raises(ValueError):
        upsert_custom_preset(conn, _preset("general"))
    assert get_preset(conn, "general")["is_active"] is True
