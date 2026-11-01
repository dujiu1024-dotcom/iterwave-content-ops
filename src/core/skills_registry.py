"""Trusted built-in Skill metadata.

This catalog describes capabilities shipped with the application. It does not load
or execute user-supplied code. Runtime execution remains owned by trusted workflow
components; custom manifests are metadata-only until a separately audited sandbox
is implemented.
"""

from __future__ import annotations


_BUILTIN_SKILLS = (
    ("内容生产", "topic_scoring", "选题评分引擎", "src/core/scoring/", "normal", "三模式自动切换"),
    ("内容生产", "prompt_variants", "四平台变体生成", "src/core/content_pipeline.py", "normal", "同选题 × 四风格"),
    ("内容生产", "ultimate_goal_check", "终极目标检查（R2）", "src/core/guard/", "normal", "三入口 check_content/strategy/comment"),
    ("内容生产", "image_card", "图片卡片制作", "src/core/content_pipeline.py", "normal", "Pillow + OpenCV"),
    ("内容生产", "draft_saver", "Playwright 存草稿", "src/adapters/", "limited", "仅执行平台存草稿，不代表发布"),
    ("运营与复盘", "circuit_breaker", "熔断器（R6）", "src/core/ops/circuit_breaker.py", "normal", "5 信号 + 3 步恢复"),
    ("运营与复盘", "rhythm_controller", "节奏控制器", "src/core/ops/rhythm.py", "normal", "平台独立配额 + 时段窗口"),
    ("运营与复盘", "ops_engine", "运营引擎", "src/core/ops/engine.py", "limited", "被动回复已接；主动评论/关注受严格配额约束"),
    ("运营与复盘", "spiral_engine", "螺旋引擎 + 缺口分析", "src/core/spiral/", "normal", "三通道输出"),
    ("运营与复盘", "zscore_normalize", "跨平台归一化（R13）", "src/core/metrics/", "normal", "平台内 z-score"),
    ("支撑层", "recovery_manager", "断点恢复（R16）", "src/core/recovery.py", "normal", "任务记录持久化 + 幂等覆盖"),
    ("支撑层", "autogrow", "自动闭环（自生长）", "src/core/autogrow.py", "normal", "复盘→评分/文案，条件满足自动开下一轮"),
    ("支撑层", "topic_pool", "选题池", "src/core/topic_pool.py", "normal", "本地库 + 热点 + 手动，软删除可恢复"),
    ("支撑层", "orchestration", "编排与诊断", "src/core/orchestration.py", "normal", "步骤开关 / 依赖校验 / 自检清单"),
    ("支撑层", "scheduler", "定时任务调度（11 类）", "src/core/scheduler/", "normal", "任务记录持久化到本地数据库"),
    ("支撑层", "platform_readback", "平台数据读回", "src/vendor/easel/", "limited", "account_stats / platform_readback 依赖有效登录态"),
)


def builtin_skills_catalog() -> list[dict]:
    """Return a fresh, read-only catalog with explicit lifecycle semantics."""
    return [
        {
            "group": group,
            "skill_id": skill_id,
            "name": name,
            "source": source,
            "capability_status": capability_status,
            "description": description,
            "installation_state": "built_in",
            "enabled_state": "system_managed",
            "runnable_state": "workflow_managed",
            "execution_mode": "trusted_application_workflow",
        }
        for group, skill_id, name, source, capability_status, description in _BUILTIN_SKILLS
    ]
