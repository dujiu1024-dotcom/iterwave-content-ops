/** 全站统一的用户可见枚举文案。数据库/API 枚举保持原值，展示层只通过本文件翻译。 */
const LABELS: Record<string, string> = {
  // 执行器、任务类型与来源
  round_executor: "轮次执行器", round_run: "内容生产任务", scheduler_job: "后台调度任务",
  round_pipeline: "轮次生产流程", round_create: "创建轮次", autogrow: "自动闭环",
  agent: "智能助手", "web-ui": "网页操作", manual: "人工填写", manual_input: "人工填写",
  platform_readback: "平台数据回读", platform_api: "平台接口", generated: "已生成",
  imported: "导入", hot_topic: "热点采集", user: "用户操作", auto: "自动触发",
  // 通用状态
  pending: "待执行", queued: "排队中", running: "执行中", success: "成功",
  succeeded: "已完成", failed: "失败", interrupted: "已中断", skipped: "已跳过",
  cancelled: "已取消", done: "已完成", completed: "已完成", created: "已创建",
  producing: "生产中", awaiting_publish: "待发布", collecting_metrics: "数据采集中",
  analyzing: "复盘中", partial_completed: "部分完成", paused: "已暂停",
  draft_pending: "待保存", pending_verify: "待核实平台保存", draft_saved: "草稿已保存",
  user_published: "用户已发布", data_fetching: "数据采集中", published: "已发布",
  expired: "已过期", active: "已启用", inactive: "已停用", archived: "已归档",
  deleted: "已删除", rejected: "已拒绝", ready: "就绪", generating: "生成中",
  quality_check: "质量检查中", reviewed: "已审核", planned: "待执行", generated_status: "已生成",
  // 生产步骤与后台任务
  publish_polling: "发布状态轮询", data_fetch: "平台数据采集", daily_ops: "每日运营",
  comment_polling: "评论轮询", cb_recovery: "平台连接恢复", kb_scan: "选题库扫描",
  kb_sync: "选题库同步", draft_save: "草稿保存", save_pending_drafts: "恢复待存草稿",
  data_retention_cleanup: "数据清理", plan_signature: "记录计划版本",
  apply_feedback: "应用复盘建议", dimension_quota: "维度配额", guard_reject: "质量检查拦截",
  auto_schedule: "自动排期", kb_read: "读取选题库", score: "选题评分",
  produce: "生成内容", save_drafts: "批量保存草稿", dry_run: "生成预览",
  // 热点、知识库与素材来源
  toutiao: "今日头条", zhihu: "知乎", weibo: "微博", bilibili: "哔哩哔哩",
  baidu: "百度", xhs: "小红书", douyin: "抖音", kuaishou: "快手",
  wechat_channels: "视频号", rss: "RSS 订阅", website: "网站", upload: "手动上传",
  generated_image: "AI 生成图片", ai_generated: "AI 生成", library: "素材库",
  // 告警级别
  debug: "调试", info: "提示", notice: "注意", warning: "警告", warn: "警告",
  error: "错误", critical: "严重", fatal: "致命",
  // 目标与评分
  goal_strategy: "目标策略", publish_decision: "发布决策",
  threshold: "阈值评分", weighted: "加权评分", hybrid: "混合评分",
  // 品牌视觉版本
  draft: "草稿", current: "当前版本", superseded: "历史版本",
  // 指标
  interaction_rate: "互动率", like_rate: "点赞率", collect_rate: "收藏率",
  follower_efficiency: "涨粉效率", views: "曝光量", impressions: "曝光量",
  likes: "点赞数", collects: "收藏数", comments: "评论数", shares: "分享数",
  new_followers: "新增粉丝", followers: "粉丝数", saves: "收藏数",
};

const METRIC_LABELS: Record<string, string> = {
  interaction_rate: "互动率", like_rate: "点赞率", collect_rate: "收藏率",
  follower_efficiency: "涨粉效率", views: "曝光量", impressions: "曝光量",
  likes: "点赞数", collects: "收藏数", comments: "评论数", shares: "分享数",
  new_followers: "新增粉丝", followers: "粉丝数", saves: "收藏数",
};

export function displayCodeLabel(value: string | null | undefined, fallback = "其他（需确认）"): string {
  if (!value) return fallback;
  return LABELS[value] ?? fallback;
}
export function displayStatusLabel(value: string | null | undefined): string {
  return displayCodeLabel(value, "其他状态（需确认）");
}
export function displayTaskTypeLabel(value: string | null | undefined): string {
  return displayCodeLabel(value, "其他任务（需确认）");
}
export function displayStepLabel(value: string | null | undefined): string {
  return displayCodeLabel(value, "其他步骤（需确认）");
}
export function displaySourceLabel(value: string | null | undefined): string {
  if (value === "generated" || value === "ai_generated" || value === "generated_image") return "AI 生成";
  return displayCodeLabel(value, "其他来源（需确认）");
}
export function displayPlatformLabel(value: string | null | undefined): string {
  return displayCodeLabel(value, "其他平台（需确认）");
}
export function displayLevelLabel(value: string | null | undefined): string {
  return displayCodeLabel(value, "其他级别（需确认）");
}
export function displayMetricLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return METRIC_LABELS[value] ?? "其他指标（需确认）";
}
export function displayErrorLabel(value: string | null | undefined): string {
  if (!value) return "未知错误";
  const errors: Record<string, string> = {
    TENANT_CONTEXT_CHANGED: "账号组上下文已变化，请刷新后重试",
    GUARD_REJECT: "内容未通过质量检查",
    RATE_LIMITED: "请求过于频繁，请稍后重试",
    TIMEOUT: "执行超时",
    PLATFORM_AUTH_EXPIRED: "平台登录状态已失效",
  };
  return errors[value] ?? "系统返回了未分类错误";
}
