import sqlite3

import pytest

from src.core.content_pipeline import image_generation_settings


def test_image_generation_settings_gracefully_handles_missing_legacy_table():
    conn = sqlite3.connect(":memory:")
    try:
        settings, reason = image_generation_settings(conn)
        assert settings is None
        assert reason == "media_generation_settings 未就绪"
    finally:
        conn.close()


def test_image_generation_settings_does_not_hide_unrelated_database_errors():
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("connection invariant broken")

    with pytest.raises(RuntimeError, match="connection invariant broken"):
        image_generation_settings(BrokenConnection())
