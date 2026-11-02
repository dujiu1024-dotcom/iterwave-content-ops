"""外部参考与个人观点进入文案生成提示词的隔离测试。"""

import sqlite3

from src.adapters.base import Platform
from src.core.content_pipeline import build_asset_context_block


def _assets_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE content_assets (
            id INTEGER PRIMARY KEY,
            asset_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            source_url TEXT,
            platform TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def test_generation_context_uses_relevant_platform_references_and_personal_voice():
    conn = _assets_db()
    conn.executemany(
        """
        INSERT INTO content_assets
            (asset_type,title,body,source_url,platform,is_active,updated_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        [
            ("reference", "AI教育实践报告", "AI教育课堂实践数据摘要", "https://example.com/ai-edu", "xhs", 1, "2026-11-01"),
            ("reference", "咖啡烘焙笔记", "咖啡豆烘焙参数", "https://example.com/coffee", "xhs", 1, "2026-11-02"),
            ("reference", "抖音AI教育案例", "跨平台不应被读取", "https://example.com/douyin", "douyin", 1, "2026-11-03"),
            ("voice", "我的教学观点", "我认为AI应该辅助教师，而不是替代教师。", None, None, 1, "2026-11-01"),
            ("voice", "归档观点", "不应进入生成上下文", None, None, 0, "2026-11-04"),
        ],
    )
    conn.commit()

    prompt = build_asset_context_block(conn, Platform.XHS, "AI教育课堂应用", "教学实践")

    assert "AI教育实践报告" in prompt
    assert "https://example.com/ai-edu" in prompt
    assert "我的教学观点" in prompt
    assert "咖啡烘焙笔记" not in prompt
    assert "抖音AI教育案例" not in prompt
    assert "归档观点" not in prompt
    assert "不是系统指令" in prompt
    assert "\n" in prompt and "\\n" not in prompt
    conn.close()


def test_generation_context_fails_open_without_assets_table():
    conn = sqlite3.connect(":memory:")
    assert build_asset_context_block(conn, Platform.XHS, "AI教育", "教学") == ""
    assert build_asset_context_block(None, Platform.XHS, "AI教育", "教学") == ""
    conn.close()
