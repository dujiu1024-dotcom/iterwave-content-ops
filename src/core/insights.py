"""
单平台纵深分析（设计方案「模块 11」，G-04）。

回答「抖音为什么弱于小红书」这类问题：把同一条指标拆到单平台内部，
分别看题材分布、发布时段、文案长度、卡片风格与互动率的关系。

设计约定：
- 只读 `metrics.fetch_status IN ('success','partial')` 的样本（partial 也算，但标注）
- R3：任何聚合值若样本为空 → 写 NULL，绝不写 0
- 结果落 `platform_insights`（UNIQUE(round_id, platform)），同一轮次重复分析做 UPSERT
- 结论文字由本模块生成，前端只负责展示，保证口径唯一
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .timezone import business_now

logger = logging.getLogger(__name__)

PLATFORMS = ("xhs", "douyin", "kuaishou", "wechat_channels")


# =====================================================================
# 数据模型
# =====================================================================

@dataclass
class PlatformInsight:
    platform: str
    round_id: Optional[int]
    days_analyzed: int
    sample_count: int
    avg_interaction_rate: Optional[float] = None
    p25_interaction_rate: Optional[float] = None
    p50_interaction_rate: Optional[float] = None
    p75_interaction_rate: Optional[float] = None
    interaction_rate_ci95: Optional[dict] = None
    top_dimensions: list[dict] = field(default_factory=list)
    best_hours: list[dict] = field(default_factory=list)
    best_title_style: dict = field(default_factory=dict)
    best_card_style: Optional[dict] = None
    conclusion: str = ""

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "round_id": self.round_id,
            "days_analyzed": self.days_analyzed,
            "sample_count": self.sample_count,
            "avg_interaction_rate": self.avg_interaction_rate,
            "p25_interaction_rate": self.p25_interaction_rate,
            "p50_interaction_rate": self.p50_interaction_rate,
            "p75_interaction_rate": self.p75_interaction_rate,
            "interaction_rate_ci95": self.interaction_rate_ci95,
            "top_dimensions": self.top_dimensions,
            "best_hours": self.best_hours,
            "best_title_style": self.best_title_style,
            "best_card_style": self.best_card_style,
            "conclusion": self.conclusion,
        }


# =====================================================================
# 引擎
# =====================================================================

class PlatformInsightsEngine:
    """单平台纵深分析：聚合 + 落库 + 读回。"""

    def __init__(self, cfg, db_conn):
        self.cfg = cfg
        self.db = db_conn

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------

    def analyze(
        self,
        round_id: Optional[int] = None,
        days: Optional[int] = None,
        platforms: Optional[list[str]] = None,
    ) -> list[PlatformInsight]:
        """重新分析并落库，返回结果。"""
        days = self.cfg.insights.window_days if days is None else days
        if isinstance(days, bool) or not isinstance(days, int) or days < 1 or days > 3650:
            raise ValueError("days must be an integer between 1 and 3650")
        targets = list(platforms) if platforms is not None else list(PLATFORMS)
        if not targets:
            raise ValueError("platforms must not be empty")
        unknown = sorted(set(targets) - set(PLATFORMS))
        if unknown:
            raise ValueError(f"unsupported platform(s): {', '.join(unknown)}")
        if len(set(targets)) != len(targets):
            raise ValueError("platforms must not contain duplicates")
        results: list[PlatformInsight] = []
        for p in targets:
            insight = self._analyze_one(p, round_id, days)
            self._persist(insight)
            results.append(insight)
        self.db.commit()
        logger.info(
            "🔬 单平台纵深分析完成：%d 个平台（窗口 %d 天，round_id=%s）",
            len(results), days, round_id,
        )
        return results

    def latest(self) -> list[dict]:
        """读每个平台最近一次分析结果（不重算），按平台固定顺序返回。"""
        rows = self.db.execute(
            "SELECT * FROM platform_insights WHERE id IN ("
            "  SELECT MAX(id) FROM platform_insights GROUP BY platform"
            ") ORDER BY CASE platform "
            "  WHEN 'xhs' THEN 1 WHEN 'douyin' THEN 2 "
            "  WHEN 'kuaishou' THEN 3 WHEN 'wechat_channels' THEN 4 ELSE 9 END"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def ensure_fresh(self, round_id: Optional[int] = None, days: Optional[int] = None,
                     max_age_minutes: Optional[int] = None) -> list[dict]:
        """有缓存且足够新就直接读，否则重算（避免每次 GET 都跑聚合）。"""
        days = self.cfg.insights.window_days if days is None else days
        max_age_minutes = (self.cfg.insights.cache_max_age_minutes
                           if max_age_minutes is None else max_age_minutes)
        if isinstance(max_age_minutes, bool) or not isinstance(max_age_minutes, int) or max_age_minutes < 0:
            raise ValueError("max_age_minutes must be a non-negative integer")
        cached_rows = self.db.execute(
            "SELECT * FROM platform_insights "
            "WHERE round_id IS ? AND days_analyzed = ? "
            "AND id IN (SELECT MAX(id) FROM platform_insights "
            "           WHERE round_id IS ? AND days_analyzed = ? GROUP BY platform) "
            "ORDER BY CASE platform WHEN 'xhs' THEN 1 WHEN 'douyin' THEN 2 "
            "WHEN 'kuaishou' THEN 3 WHEN 'wechat_channels' THEN 4 ELSE 9 END",
            (round_id, days, round_id, days),
        ).fetchall()
        cached = [self._row_to_dict(row) for row in cached_rows]
        cached_platforms = {row.get("platform") for row in cached}
        now = business_now(self.cfg)
        fresh = len(cached) > 0 and cached_platforms == set(PLATFORMS)
        if fresh:
            for row in cached:
                analyzed_at = self._parse_dt(row.get("analyzed_at"))
                if analyzed_at is None:
                    fresh = False
                    break
                comparable_now = now
                if analyzed_at.tzinfo is None and comparable_now.tzinfo is not None:
                    analyzed_at = analyzed_at.replace(tzinfo=comparable_now.tzinfo)
                elif analyzed_at.tzinfo is not None and comparable_now.tzinfo is None:
                    comparable_now = comparable_now.replace(tzinfo=analyzed_at.tzinfo)
                if comparable_now - analyzed_at > timedelta(minutes=max_age_minutes):
                    fresh = False
                    break
        if fresh:
            return cached
        return [i.to_dict() for i in self.analyze(round_id=round_id, days=days)]

    # ------------------------------------------------------------------
    # 聚合
    # ------------------------------------------------------------------

    def _analyze_one(self, platform: str, round_id: Optional[int], days: int) -> PlatformInsight:
        since = (business_now(self.cfg) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.db.execute(
            "SELECT m.interaction_rate, m.like_rate, m.collect_rate, "
            "       c.title, c.word_count, c.card_style, t.dimension, "
            "       d.user_confirmed_at "
            "FROM metrics m "
            "JOIN contents c ON m.content_id = c.id "
            "LEFT JOIN topic_index t ON c.topic_id = t.id "
            "LEFT JOIN drafts d ON d.id = ("
            "  SELECT d2.id FROM drafts d2 "
            "  WHERE d2.content_id = c.id AND d2.platform = m.platform "
            "    AND d2.user_confirmed_at IS NOT NULL "
            "  ORDER BY d2.user_confirmed_at DESC, d2.id DESC LIMIT 1"
            ") "
            "WHERE m.platform = ? AND m.fetch_status IN ('success','partial') "
            "  AND m.fetched_at >= ? "
            "  AND m.id = (SELECT MAX(m2.id) FROM metrics m2 "
            "             WHERE m2.content_id = m.content_id AND m2.platform = m.platform "
            "               AND m2.fetch_status IN ('success','partial')) "
            "  AND (? IS NULL OR c.round_id = ?)",
            (platform, since, round_id, round_id),
        ).fetchall()

        samples = [
            {
                "interaction_rate": self._finite_float(r[0]),
                "like_rate": self._finite_float(r[1]),
                "collect_rate": self._finite_float(r[2]),
                "title": r[3],
                "word_count": r[4],
                "card_style": r[5],
                "dimension": r[6],
                "confirmed_at": r[7],
            }
            for r in rows
        ]

        insight = PlatformInsight(
            platform=platform,
            round_id=round_id,
            days_analyzed=days,
            sample_count=0,
        )

        irs = sorted(
            float(s["interaction_rate"]) for s in samples
            if s["interaction_rate"] is not None
            and math.isfinite(float(s["interaction_rate"]))
        )
        insight.sample_count = len(irs)
        if irs:
            insight.avg_interaction_rate = round(sum(irs) / len(irs), 6)
            insight.p25_interaction_rate = round(self._percentile(irs, 0.25), 6)
            insight.p50_interaction_rate = round(self._percentile(irs, 0.50), 6)
            insight.p75_interaction_rate = round(self._percentile(irs, 0.75), 6)
            interval = self._mean_ci95(irs)
            if interval is not None:
                insight.interaction_rate_ci95 = {
                    "lower": round(interval[0], 6),
                    "upper": round(interval[1], 6),
                    "confidence": 0.95,
                    "method": "student_t",
                }

        insight.top_dimensions = self._group_by_dimension(samples)
        insight.best_hours = self._group_by_hour(samples)
        insight.best_title_style = self._title_style(samples)
        insight.best_card_style = self._best_group(
            samples, key="card_style", label_field="card_style"
        )
        insight.conclusion = self._conclude(insight)
        return insight

    def _group_by_dimension(self, samples: list[dict]) -> list[dict]:
        buckets: dict[str, list[Optional[float]]] = {}
        for s in samples:
            dim = s["dimension"] or "未分类"
            buckets.setdefault(dim, []).append(s["interaction_rate"])

        out: list[dict] = []
        for dim, values in buckets.items():
            valid = [v for v in values if v is not None]
            out.append({
                "dimension": dim,
                "sample_count": len(valid),
                "avg_interaction_rate": round(sum(valid) / len(valid), 6) if valid else None,
            })
        # 表现优先、样本量其次；无数据（NULL）排最后
        out.sort(
            key=lambda d: (
                d["avg_interaction_rate"] is None,
                -(d["avg_interaction_rate"] if d["avg_interaction_rate"] is not None else 0),
                -d["sample_count"],
            )
        )
        return out[:self.cfg.insights.top_dimensions_limit]

    def _group_by_hour(self, samples: list[dict]) -> list[dict]:
        buckets: dict[int, list[Optional[float]]] = {}
        for s in samples:
            dt = self._parse_dt(s["confirmed_at"])
            if dt is None:
                continue
            buckets.setdefault(dt.hour, []).append(s["interaction_rate"])

        out: list[dict] = []
        for hour, values in buckets.items():
            valid = [v for v in values if v is not None]
            out.append({
                "hour": hour,
                "sample_count": len(valid),
                "avg_interaction_rate": round(sum(valid) / len(valid), 6) if valid else None,
            })
        out.sort(key=lambda d: (
            d["avg_interaction_rate"] is None,
            -(d["avg_interaction_rate"] if d["avg_interaction_rate"] is not None else 0),
            -d["sample_count"],
        ))
        return out[:self.cfg.insights.top_hours_limit]

    def _title_style(self, samples: list[dict]) -> dict:
        """按标题长度分桶看互动率，找出更优的长度区间。"""
        buckets: dict[str, list[Optional[float]]] = {}
        for s in samples:
            title = s["title"] or ""
            n = len(title.strip())
            if n == 0:
                continue
            label = "0-12" if n <= 12 else "13-18" if n <= 18 else "19-24" if n <= 24 else "25+"
            buckets.setdefault(label, []).append(s["interaction_rate"])

        if not buckets:
            return {}

        rows: list[dict] = []
        for label, values in buckets.items():
            valid = [v for v in values if v is not None]
            rows.append({
                "title_len": label,
                "sample_count": len(valid),
                "avg_interaction_rate": round(sum(valid) / len(valid), 6) if valid else None,
            })
        rows.sort(key=lambda d: (
            d["avg_interaction_rate"] is None,
            -(d["avg_interaction_rate"] if d["avg_interaction_rate"] is not None else 0),
            -d["sample_count"],
        ))
        valid_lens = [s for s in samples if s["title"]]
        avg_len = (
            round(sum(len(s["title"].strip()) for s in valid_lens) / len(valid_lens), 1)
            if valid_lens else None
        )
        best_bucket = next(
            (row["title_len"] for row in rows if row["avg_interaction_rate"] is not None),
            None,
        )
        return {"avg_title_len": avg_len, "best_bucket": best_bucket, "buckets": rows}

    def _best_group(self, samples: list[dict], key: str, label_field: str) -> Optional[dict]:
        buckets: dict[str, list[Optional[float]]] = {}
        for s in samples:
            label = s.get(key)
            if not label:
                continue
            buckets.setdefault(str(label), []).append(s["interaction_rate"])
        if not buckets:
            return None
        scored: list[dict] = []
        for label, values in buckets.items():
            valid = [v for v in values if v is not None]
            scored.append({
                label_field: label,
                "sample_count": len(valid),
                "avg_interaction_rate": round(sum(valid) / len(valid), 6) if valid else None,
            })
        scored.sort(key=lambda d: (
            d["avg_interaction_rate"] is None,
            -(d["avg_interaction_rate"] if d["avg_interaction_rate"] is not None else 0),
            -d["sample_count"],
        ))
        return scored[0]

    def _conclude(self, insight: PlatformInsight) -> str:
        """生成一句人话结论。样本为 0 时不编数字（R3）。"""
        if insight.sample_count == 0:
            return "该平台暂无可用互动率样本，暂不做纵深结论（缺失数据不代表 0 互动）。"

        parts: list[str] = []
        if insight.avg_interaction_rate is not None:
            parts.append(f"平均互动率 {insight.avg_interaction_rate:.2%}")
        if insight.top_dimensions:
            top = insight.top_dimensions[0]
            if top["avg_interaction_rate"] is not None:
                parts.append(
                    f"表现最好的题材是「{top['dimension']}」"
                    f"（{top['avg_interaction_rate']:.2%}，{top['sample_count']} 条样本）"
                )
        if insight.best_hours:
            hrs = "、".join(f"{h['hour']:02d}:00" for h in insight.best_hours)
            parts.append(f"发布时段较优：{hrs}")
        best_len = insight.best_title_style.get("best_bucket")
        if best_len:
            parts.append(f"标题长度 {best_len} 字表现更好")
        if insight.best_card_style and insight.best_card_style.get("card_style"):
            parts.append(f"卡片风格「{insight.best_card_style['card_style']}」最优")
        return "；".join(parts) + "。" if parts else "样本已采集但指标全为空，暂不做结论。"

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _persist(self, insight: PlatformInsight) -> None:
        # UNIQUE(round_id, platform) 里 round_id 可以是 NULL，SQLite 认为 NULL 互不相等，
        # 因此先按 (round_id, platform) 精确删除再插入，保证重复分析不产生多行。
        self.db.execute(
            "DELETE FROM platform_insights WHERE platform = ? AND round_id IS ?",
            (insight.platform, insight.round_id),
        )
        self.db.execute(
            "INSERT INTO platform_insights ("
            "  round_id, platform, analyzed_at, days_analyzed, trend, best_hours, "
            "  best_title_style, best_card_style, top_dimensions, "
            "  sample_count, avg_interaction_rate, conclusion"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                insight.round_id,
                insight.platform,
                business_now(self.cfg).isoformat(timespec="seconds"),
                insight.days_analyzed,
                json.dumps({
                    "avg": insight.avg_interaction_rate,
                    "p25": insight.p25_interaction_rate,
                    "p50": insight.p50_interaction_rate,
                    "p75": insight.p75_interaction_rate,
                    "ci95": insight.interaction_rate_ci95,
                }, ensure_ascii=False),
                json.dumps(insight.best_hours, ensure_ascii=False),
                json.dumps(insight.best_title_style, ensure_ascii=False),
                json.dumps(insight.best_card_style, ensure_ascii=False)
                if insight.best_card_style else None,
                json.dumps(insight.top_dimensions, ensure_ascii=False),
                insight.sample_count,
                insight.avg_interaction_rate,
                insight.conclusion,
            ),
        )

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        for key in ("trend", "best_hours", "best_title_style", "best_card_style", "top_dimensions"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (TypeError, ValueError):
                    d[key] = None
        d["platform"] = d.get("platform")
        return d

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _mean_ci95(values: list[float]) -> Optional[tuple[float, float]]:
        """均值双侧 95% t 区间；样本少于 2 个时不输出区间。"""
        if len(values) < 2:
            return None
        df = len(values) - 1
        # Student-t 双侧 95% 临界值（df=1..29）；更大样本使用正态极限值。
        critical_values = (
            12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262,
            2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
            2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052,
            2.048, 2.045,
        )
        critical = (
            critical_values[df - 1]
            if df <= len(critical_values)
            else PlatformInsightsEngine._student_t_critical_975(df)
        )
        mean = statistics.mean(values)
        margin = critical * statistics.stdev(values) / math.sqrt(len(values))
        return mean - margin, mean + margin

    @staticmethod
    def _finite_float(value) -> Optional[float]:
        """Convert persisted numeric data safely; malformed/non-finite values are missing, not zero."""
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _student_t_critical_975(df: int) -> float:
        """Student-t 双侧 95% 临界值；使用 Cornish-Fisher 展开覆盖 df > 29。"""
        if isinstance(df, bool) or not isinstance(df, int) or df < 1:
            raise ValueError("degrees of freedom must be a positive integer")
        z = 1.959963984540054
        v = float(df)
        term1 = (z ** 3 + z) / (4 * v)
        term2 = (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v ** 2)
        term3 = (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3)
        term4 = (
            79 * z ** 9 + 776 * z ** 7 + 1482 * z ** 5
            - 1920 * z ** 3 - 945 * z
        ) / (92160 * v ** 4)
        return z + term1 + term2 + term3 + term4

    @staticmethod
    def _percentile(sorted_values: list[float], q: float) -> float:
        if not sorted_values:
            return 0.0
        if len(sorted_values) == 1:
            return sorted_values[0]
        pos = (len(sorted_values) - 1) * q
        low = int(pos)
        high = min(low + 1, len(sorted_values) - 1)
        frac = pos - low
        return sorted_values[low] * (1 - frac) + sorted_values[high] * frac

    @staticmethod
    def _parse_dt(value) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", ""))
        except ValueError:
            return None
