// ===== API Client — 所有后端调用集中在这里 =====

// Platform 类型 + 元数据：唯一来源在 platforms.ts
import type { Platform as _Platform } from "./platforms";
export type Platform = _Platform;
export { platformMeta, PLATFORM_META, ALL_PLATFORMS } from "./platforms";

export interface RecoveryInfo {
  interrupted_tasks: number;
  requeued_data_fetch: string[];
  stale_rounds: number[];
  pending_verify: number;
}

export interface GlobalTaskQueueItem {
  queue_order: number;
  task_id: string;
  task_type: string;
  tenant_id: string;
  tenant_name: string;
  aggregate_id: string | null;
  /** round_run 任务对应的业务轮次编号（spiral_rounds.round_number）；无法解析时为 null */
  round_number?: number | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  created_at: string;
  available_at: string;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  error_code: string | null;
  error_message: string | null;
  retry_of_task_id?: string | null;
  retry_confirmed_by?: string | null;
  retry_confirmation_note?: string | null;
  resolution_action?: "handled" | "ignored" | null;
  resolution_actor?: string | null;
  resolution_note?: string | null;
  resolution_at?: string | null;
}

export interface GlobalTaskQueueResponse {
  items: GlobalTaskQueueItem[];
  total?: number;
  page?: number;
  page_size?: number;
  pages?: number;
  status_counts?: { queued?: number; running?: number; failed?: number; interrupted?: number };
  tenant_status_counts?: Record<string, { queued?: number; running?: number; failed?: number; interrupted?: number }>;
}

export interface GlobalTodoItem {
  id: string;
  record_id: number | string;
  source: "global_queue" | "task_run" | "ops_log" | "circuit_breaker" | "hot_topic_source" | "media_review" | "notification" | "media_generation";
  source_label: string;
  tenant_id: string;
  tenant_name: string;
  title: string;
  detail: string;
  status: string;
  created_at: string | null;
  severity: "high" | "medium";
  target_page: string;
  target_tab: string | null;
}

export interface GlobalTodoResponse {
  items: GlobalTodoItem[];
  total: number;
  counts: Record<GlobalTodoItem["source"], number>;
  page: number;
  page_size: number;
  pages: number;
  capped: boolean;
}

export interface QueueTaskSubmission {
  round_id: number;
  task_id: string;
  started: boolean;
  created?: boolean;
  running: boolean;
  queued: boolean;
  status: GlobalTaskQueueItem["status"];
}

export interface SystemStatus {
  enabled_platforms: Platform[];
  /** 兼容旧字段：实际表示本机登录态文件存在，不代表会话有效 */
  logged_in_platforms: Platform[];
  credential_present_platforms?: Platform[];
  platform_status?: Partial<Record<Platform, PlatformProgress>>;
  /** 无登录态的平台：真实操作为阻塞状态 */
  platforms_without_login: Platform[];
  circuit_broken_platforms: Platform[];
  active_rounds: number;
  published_samples: number;
  scoring_mode: string;
  recovery: RecoveryInfo;
}

export interface Goal {
  id: string;
  goal_text: string;
  target_metric: string;
  target_value: number;
  target_platform: Platform | null;
  metric_scope: string;
  baseline_value: number | null;
  baseline_at: string | null;
  metric_period_start: string | null;
  metric_period_end: string | null;
  data_source: string | null;
  current_value: number | null;
  progress_updated_at: string | null;
  /** 后端算好的达成率（%）；任一分量 NULL → null（R21） */
  achievement_rate: number | null;
  status: string;
  created_at: string;
  /** north_star = 总目标；platform = 平台阶段目标（旧数据为 platform） */
  goal_level?: "north_star" | "platform" | null;
  parent_goal_id?: string | null;
  weight?: number | null;
  contribution_note?: string | null;
}

export type GoalMode = "unified" | "per_platform" | "hybrid";

export interface PlatformPersonaConfig {
  inherit_global: boolean;
  account_name: string;
  positioning: string;
  audience: string;
  role: string;
  persona_keywords: string[];
  forbidden_directions: string[];
  extra_redlines: string[];
}

export interface EffectivePersona {
  platform: string | null;
  source: "global" | "merged" | "platform";
  positioning: string;
  audience: string;
  role: string;
  account_name: string;
  persona_keywords: string[];
  forbidden_directions: string[];
  redline_keywords: string[];
}

export interface PlatformLink {
  source: Platform;
  target: Platform;
  relation: "traffic" | "reuse" | "cross";
  note: string;
}

export interface PersonasView {
  goal_mode: GoalMode;
  global: { positioning: string; persona_keywords: string[]; forbidden_directions: string[]; redline_keywords_count: number };
  platforms: Record<Platform, { config: PlatformPersonaConfig; effective: EffectivePersona; has_own: boolean }>;
  links: PlatformLink[];
  enabled_platforms: Platform[];
}

export interface SpiralRound {
  id: number;
  goal_id: string | null;
  round_number: number;
  status: string;
  topic_ids: string;
  content_variants: string;
  plan_signature: string | null;
  created_at: string;
  awaiting_publish_ended_at: string | null;
  collecting_metrics_started_at: string | null;
}

export interface SpiralRoundPage {
  items: SpiralRound[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface OnboardingProgress {
  step_key: string | null;
  step_index: number | null;
  updated_at: string | null;
}

export interface BusinessAnalyticsSummary {
  window_days: number;
  since: string;
  contents_created: number;
  drafts_created: number;
  confirmed_published: number;
  interactions: {
    sample_count: number;
    views: number | null;
    likes: number | null;
    collects: number | null;
    comments: number | null;
    shares: number | null;
    new_followers: number | null;
    known_samples: Record<string, number>;
  };
  followers_growth_by_platform: Record<string, {
    growth: number; baseline_at: string; latest_at: string;
  } | null>;
}

export interface MetricsResponse {
  items: MetricRow[];
  total: number;
  z_stats: {
    platform: string; metric: string;
    mean: number; std: number; sample_count: number;
  }[];
  z_window_days: number;
  /** 可跨平台对比的比率指标（R13） */
  cross_platform_comparable: string[];
  /** 只允许平台内 z 的原始计数指标 */
  platform_only_metrics: string[];
  page?: number; page_size?: number; pages?: number;
}

export interface MetricRow {
  id?: number;
  platform: Platform;
  content_id: string | null;
  content_title?: string | null;
  round_id?: number | null;
  views: number | null;
  likes: number | null;
  collects: number | null;
  comments: number | null;
  shares: number | null;
  new_followers: number | null;
  interaction_rate: number | null;
  like_rate: number | null;
  collect_rate: number | null;
  fetch_status: string;
  fetched_at: string | null;
  attempt?: number | null;
  /** P1-8: 采集失败原因（来自 metrics.error_msg，failed/parse_error 时有值） */
  error_msg?: string | null;
  /** 平台内部 z-score；样本不足或原值为 NULL 时为 null（R3） */
  z: Record<string, number | null>;
}

export interface Draft {
  id: string;
  content_id: string;
  platform: Platform;
  status: string;
  saved_at: string | null;
  user_confirmed_at: string | null;
  error_msg: string | null;
  created_at: string;
  /** LEFT JOIN contents 带出，列表接口可能为空 */
  title?: string | null;
  round_id?: number | null;
  topic_id?: string | null;
  visual_status?: "degraded" | "media_asset" | "generated" | null;
  visual_reason?: string | null;
  scheduled_publish_at?: string | null;
  /** 用户补填的平台笔记链接（被动评论轮询靠它定位笔记） */
  platform_note_url?: string | null;
  guard_outcome?: "pass" | "warn" | "fail" | "error" | null;
  guard_reason?: string | null;
  review_rejected?: boolean;
  review_rejection_reason?: string | null;
  saved_content_hash?: string | null;
  /** draft_pending=仅本地；draft_saved=matched / stale / unknown。 */
  platform_content_sync?: "local_only" | "matched" | "stale" | "unknown" | null;
  /** 当前文案是否可按正常路径确认发布；质检/人工审核未放行时需要显式例外原因。 */
  guard_confirmation_allowed?: boolean;
}

export interface DraftPage {
  items: Draft[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/** POST /api/rounds/{id}/run 的执行报告（RoundExecutor.run 返回） */
export interface PendingDraftSaveResult {
  round_id: number;
  round_number: number;
  drafts_attempted: number;
  drafts_saved: number;
  drafts_failed: number;
  pending_drafts: number;
  final_status: string;
  errors: string[];
}

export interface RunRoundReport {
  round_id: number;
  round_number: number;
  started_at: string;
  finished_at?: string;
  platforms: Platform[];
  topic_count_requested: number;
  steps: { step: string; [key: string]: unknown }[];
  contents_created: number;
  drafts_created: number;
  drafts_saved: number;
  drafts_failed: number;
  guard_rejected: number;
  retry_counts?: Record<string, number>;
  errors: string[];
  final_status: string | null;
  plan_signature?: string;
}

export interface ScoredTopic {
  id: string;
  title: string;
  dimension: string;
  total_score: number;
  is_exploration: boolean;
  breakdown: Record<string, { raw: number; weight: number; score: number }>;
}

export interface GapSuggestion {
  dimension: string;
  gap_type: string;
  audience: string;
  suggestion_text: string;
  current_count: number;
  target_count: number | null;
  sample_count: number;
  avg_interaction_rate: number | null;
  confidence: string;
}

export interface SpiralAnalysis {
  round_id: number;
  gaps: GapSuggestion[];
  strategy_adjustments: { type: string; description: string }[];
  ops_adjustments: string[];
  evidence: Record<string, unknown>;
  /** G-04 单平台纵深（第四通道） */
  platform_breakdown?: PlatformInsight[];
}

// G-04 单平台纵深分析结果（数字可为 null：样本不足 ≠ 0）
export interface PlatformInsight {
  platform: Platform;
  round_id: number | null;
  days_analyzed: number;
  sample_count: number;
  avg_interaction_rate: number | null;
  p25_interaction_rate: number | null;
  p50_interaction_rate: number | null;
  p75_interaction_rate: number | null;
  top_dimensions: { dimension: string; sample_count: number; avg_interaction_rate: number | null }[];
  best_hours: { hour: number; sample_count: number; avg_interaction_rate: number | null }[];
  best_title_style: { avg_title_len?: number | null; best_bucket?: string; buckets?: { title_len: string; sample_count: number; avg_interaction_rate: number | null }[] };
  best_card_style: { card_style: string; sample_count: number; avg_interaction_rate: number | null } | null;
  conclusion: string;
}

export interface CircuitBreakerEvent {
  id: number;
  platform: Platform;
  reason: string;
  detected_at: string;
  cooldown_until: string | null;
  resolved_at: string | null;
  is_broken?: boolean;
}

// 热点雷达（R22：采集失败不给数字）
export interface HotTopicItem {
  id: string;
  title: string;
  source: string;
  discovered_at: string;
  url: string | null;
  heat: number | null;
  heat_unit: string | null;
  time_sensitive: number | null;
  evidence_status: "collected" | "demo" | "failed";
  dimension: string | null;
  /** true 与账号定位关联 / false 已研判无关或落入禁止方向 / null 待研判 */
  dimension_match: boolean | null;
  dimension_hits: string[];
  /** 0–100 关联分；null = 未研判（不是 0 分） */
  match_score?: number | null;
  match_angle?: string | null;
  match_reason?: string | null;
  match_method?: "keyword" | "llm" | null;
  last_seen_at?: string | null;
  /** 本轮采集到这条热点的时间（= 最后一次上榜时间） */
  collected_at?: string | null;
  /** 热度趋势辅助判断：爆发 / 持续爆发 / 降温中 / 已降温 / 平稳 */
  trend?: "爆发" | "持续爆发" | "降温中" | "已降温" | "平稳";
  trend_reason?: string;
  heat_prev?: number | null;
  heat_peak?: number | null;
  platform?: string;
  /** 被采集到的次数（去重后同一条只更新、不新增） */
  seen_count?: number;
  /** 跨平台同一事件（去重规则 2 合并到本条） */
  also_on?: { platform: string; title: string; url: string | null; heat: number | null; heat_unit: string | null }[];
  publishable: boolean;
  publishability_status?: "可发布" | "需人工确认" | "不可发布" | "未研判";
  sensitive_hits?: string[];
  /** 分平台画像模式下：适合哪些平台的账号 */
  fit_platforms?: string[];
  redline_hits: string[];
}

/** /api/insights 返回的每平台复盘洞察 */
export interface InsightItem {
  id: number;
  round_id: number;
  platform: Platform;
  analyzed_at: string;
  days_analyzed: number;
  trend: { avg: number | null; p25: number | null; p50: number | null; p75: number | null };
  best_hours: number[];
  best_title_style: Record<string, unknown>;
  best_card_style: string | null;
  top_dimensions: string[];
  sample_count?: number;
  avg_interaction_rate?: number | null;
  /** 后端实际返回的结论文案 */
  conclusion?: string;
  summary?: string;
  confidence?: number;
  evidence_days: { date: string; avg: number | null; drafts: number }[];
  suggestions: string[];
}

/** /api/goals/summary：总体目标进度 */
export interface GoalSummary {
  /** 0–100；null = 没有任何活跃目标有数据（R21：未知 ≠ 0） */
  percent: number | null;
  method: "equal_weight_mean" | string;
  active_goals: number;
  counted_goals: number;
  pending_goals: number;
  period_start: string | null;
  period_end: string | null;
  days_left: number | null;
  mode?: GoalMode;
  north_star?: { id: string; goal_text: string; rate: number | null } | null;
  by_platform?: Record<string, { percent: number | null; goals: number; counted: number }>;
}

/** 四平台进度（/api/platform_progress，固定返回 4 个平台） */
export interface PlatformProgress {
  /** 与系统配置相同的四平台支持集合 */
  supported?: boolean;
  configured?: boolean;
  enabled: boolean;
  account_registered?: boolean;
  account_active?: boolean;
  /** 本机 storage_state 文件存在；不代表平台会话仍有效 */
  credential_present?: boolean;
  session_validity?: "not_checked" | string;
  preflight_ready?: boolean;
  blockers?: string[];
  total_drafts: number;
  /** { draft_saved: 3, user_published: 1, data_fetching: 2, ... } */
  status_breakdown: Record<string, number>;
  circuit_broken: boolean;
}

/** 待处理/阻塞事项（/api/action_items，字段以后端 main.py action_items() 为准） */
export interface ActionItem {
  id: string;
  /** goal_strategy | publish_decision | circuit_recover | circuit_active */
  type: string;
  title: string;
  detail?: string;
  priority?: "high" | "medium" | "low";
  source?: string;
  created_at?: string | null;
  actions?: string[];
  target_page?: string;
  target_tab?: string | null;
}

export interface HotSourceStatus {
  platform: string;
  source: string;
  status: "collected" | "demo" | "failed";
  message: string | null;
  discovered_at: string | null;
}

export interface HotRefreshProgress {
  stage: "fetching" | "saving" | "dedup" | "matching" | "matching_partial" | "done" | "failed";
  label: string;
  percent: number;
  message?: string | null;
  /** 各数据源实时状态（抓取阶段逐条更新） */
  sources?: {
    key: string;
    label: string;
    status: "pending" | "collected" | "failed";
    items?: number;
  }[];
  updated_at?: string;
}

export interface HotRefreshJob {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  trigger: string | null;
  report: Record<string, string> | null;
  match: Record<string, unknown> | null;
  error: string | null;
  /** 采集进度：服务端后台线程实时写入，切换页签不影响采集 */
  progress?: HotRefreshProgress | null;
}

export interface HotResearchJob {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  report: Record<string, unknown> | null;
  error: string | null;
  /** 采集中时不重复启动，返回原因 */
  reason?: string;
}

export interface HotTopicsResponse {
  enabled: boolean;
  cached: boolean;
  refreshed_at: string | null;
  /** 距上次采集超过刷新间隔 */
  stale?: boolean;
  /** 后台采集任务状态（GET 只读库，采集由定时任务 / POST refresh 完成） */
  refresh_job?: HotRefreshJob;
  /** 大模型研判任务状态（POST research 触发） */
  research_job?: HotResearchJob;
  /** 大模型是否可用（未启用时「研判热点」按钮置灰） */
  research_ready?: boolean;
  match?: { threshold: number; matched: number; rejected: number; pending: number; method: string | null };
  refresh_interval_minutes?: number | null;
  refresh_schedule?: string;
  refresh_enabled?: boolean;
  storage?: { rows: number; retention_days: number; max_rows: number };
  sources: HotSourceStatus[];
  items: HotTopicItem[];
  total?: number; page?: number; page_size?: number; pages?: number; urgent_total?: number; source_options?: string[];
  filters?: Record<string, unknown>;
  message?: string;
}

export interface ScheduleEvent {
  id: string;
  title: string;
  platform: Platform;
  status: string;
  round_id: number | null;
  scheduled: string | null;
  saved_at: string | null;
  day: number | null;
}

export interface BuiltinSkillManifest {
  group: string;
  skill_id: string;
  name: string;
  capability_status: "normal" | "limited";
  description: string;
  installation_state: "built_in";
  enabled_state: "system_managed";
  runnable_state: "workflow_managed";
  execution_mode: "trusted_application_workflow";
}

export interface TenantSkillManifest {
  skill_id: string;
  name: string;
  version: string;
  description: string;
  category: string;
  inputs: Array<Record<string, unknown>>;
  outputs: Array<Record<string, unknown>>;
  permissions: string[];
  enabled: boolean;
  manifest_version: number;
  created_at: string;
  updated_at: string;
  installation_state: "registered";
  enabled_state: "enabled" | "disabled";
  runnable_state: "blocked_no_sandbox";
  execution_mode: "metadata_only";
}

export interface ContentPlan {
  id: string;
  title: string;
  topic: string;
  platforms: Platform[];
  planned_count: number;
  scheduled_date: string;
  status: "planned" | "generating" | "generated" | "reviewed" | "archived" | "cancelled" | "failed";
  notes: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ScheduleResponse {
  start_date: string;
  end_date: string;
  items: ScheduleEvent[];
  /** 未排期草稿（scheduled_publish_at IS NULL），供日历直接挑选排期 */
  unscheduled?: UnscheduledDraft[];
}

export interface UnscheduledDraft {
  id: string;
  platform: Platform;
  status: string;
  title: string | null;
  round_id: number | null;
}

/** G-13 内容资产（外部参考 / 个人观点 / 内容模板） */
export type AssetType = "reference" | "voice" | "template";

export interface AssetPage {
  items: AssetItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface TenantItem {
  id: string;
  name: string;
  created_at: string | null;
  is_active: boolean;
}
export interface TenantListResponse {
  runtime_tenant_id: string;
  selected_tenant_id: string;
  restart_required: boolean;
  /** 即时切换需在服务端显式设置 ITERWAVE_ENABLE_TENANT_HOT_SWITCH=true。 */
  hot_switch_enabled?: boolean;
  items: TenantItem[];
}
export interface GlobalMonitorTenant {
  tenant_id: string;
  tenant_name: string;
  created_at: string | null;
  db_available: boolean;
  /** uninitialized: DB file has not been created; unavailable: file exists but cannot be opened. */
  db_status?: "uninitialized" | "available" | "unavailable";
  contents_period: number;
  contents_all: number;
  drafts_all: number;
  drafts_pending: number;
  drafts_saved: number;
  drafts_published: number;
  drafts_failed: number;
  media_active: number;
  media_usage: number;
  registered_accounts?: number;
  registered_accounts_active?: number;
  recent_execution?: {
    task_type: string;
    status: string;
    has_error_detail: boolean;
    started_at: string | null;
    finished_at: string | null;
    created_at: string | null;
  } | null;
  runtime?: {
    available: boolean;
    enabled_platforms: Platform[];
    logged_in_platforms: Platform[];
    platforms_without_login: Platform[];
    circuit_broken_platforms: Platform[];
    scheduler_running: boolean | null;
    scheduler_job_count: number | null;
    scheduler_enabled_count: number | null;
    active_rounds: number | null;
  };
  platforms: Record<string, { contents: number; contents_period: number; drafts: number; registered_accounts?: number }>;
}
export interface GlobalMonitorResponse {
  days: number;
  since: string;
  totals: {
    tenants: number; available_tenants: number; registered_accounts?: number; registered_accounts_active?: number; contents_period: number; contents_all: number;
    drafts_all: number; drafts_pending: number; drafts_saved: number; drafts_published: number;
    drafts_failed: number; media_active: number; media_usage: number;
  };
  platform_totals: Record<string, { contents: number; drafts: number; registered_accounts?: number }>;
  tenants: GlobalMonitorTenant[];
  account_count_note: string;
}
export interface TenantBackupItem {
  backup_name: string;
  size_bytes: number;
  created_at: string;
}
export interface MediaGenerationSettings {
  enabled: boolean;
  monthly_quota: number;
  prompt_prefix: string;
  model: string;
  size: "1024x1024" | "1024x1536" | "1536x1024";
  updated_at: string | null;
  used_this_month: number;
  provider_configured: boolean;
  /** 图片兜底模型（火山方舟），仅用于展示；地址与密钥保存在服务端 */
  fallback_model?: string;
}
export interface MediaUsageTarget {
  id: string;
  title: string;
  platform: Platform;
  round_id: number;
  created_at: string | null;
  visual_status: "degraded" | "media_asset" | "generated";
  visual_reason: string | null;
  draft_status: "draft_pending" | "pending_verify" | "draft_saved";
}

export interface MediaRecommendation {
  asset_id: number;
  title: string;
  score: number;
  matched_keywords: string[];
  purpose_matches: string[];
  quality_score: number | null;
  width: number | null;
  height: number | null;
  reason: string;
}

export interface MediaAsset {
  id: number;
  title: string;
  original_filename: string;
  mime_type: string;
  byte_size: number;
  sha256?: string;
  width: number;
  height: number;
  alt_text: string;
  tags: string[];
  source_type: "upload" | "generated" | "imported";
  source_ref?: string | null;
  status: "active" | "archived";
  review_status: "pending_review" | "approved" | "rejected";
  review_note?: string | null;
  quality_score?: number | null;
  quality_details?: { reasons?: string[]; disclaimer?: string; method?: string } | null;
  created_at?: string;
  updated_at?: string;
  file_url: string;
  duplicate?: boolean;
}

export interface MediaAssetPage {
  items: MediaAsset[];
  total: number;
  counts: Record<"all" | "pending" | "approved" | "rejected" | "archived", number>;
  page: number;
  page_size: number;
  pages: number;
  limit: number;
  offset: number;
}

export interface AssetItem {
  id: number;
  asset_type: AssetType;
  title: string;
  body: string | null;
  source_url: string | null;
  platform: Platform | null;
  created_at: string | null;
  updated_at: string | null;
  version?: number;
  /** 后端统一序列化为 bool（数据库存 0/1） */
  is_active?: boolean;
}

/** G-13 / G-24 运行时配置（白名单可写字段 + R22 硬锁 + 审计） */
export interface ConfigMetadataItem {
  key: string;
  type: string;
  value: unknown;
  min: number | null;
  max: number | null;
  editable: boolean;
  tenant_scoped: boolean;
  restart_required: boolean;
  sensitive: boolean;
  description: string;
}

export interface ConfigAuditItem {
  id: number; key: string; old_value: string | null; new_value: string | null; changed_at: string | null;
}
export interface ConfigAuditPage extends PageResult<ConfigAuditItem> {}

export interface SystemConfigView {
  editable: {
    business_timezone: string;
    daily_quotas: Record<string, { passive_replies: number | null; active_comments: number | null; follows: number | null }>;
    ops_window: { start_hour: number | null; end_hour: number | null };
    cooldown_minutes: number | null;
    hot_topics: {
      heat_threshold: number | null;
      heat_filter_enabled: boolean | null;
      time_window_hours: number | null;
      time_filter_enabled: boolean | null;
    };
  };
  /** R22 两道硬锁：只读，后端拒绝关闭 */
  locked: { dimension_match_required: boolean; publishability_required: boolean };
  /** 账号画像 + KB + LLM + 平台连接：展示值；可编辑项走 PATCH /api/config 白名单 */
  readonly: {
    persona_configured: boolean;
    persona_keywords: string[];
    forbidden_directions: string[];
    redline_keywords_count: number;
    positioning?: string;
    goal_mode?: string;
    // KB 配置
    kb_path: string;
    kb_type: string;
    kb_table: string;
    kb_field_mapping: Record<string, string>;
    kb_default_dimension?: string;
    render_font_path?: string;
    render_card_tag_fallback?: string;
    // LLM 配置
    llm_provider: string;
    llm_model: string;
    llm_fallback_provider: string;
    llm_fallback_model: string;
    llm_base_url: string;
    llm_fallback_base_url: string;
    llm_thinking: "" | "enabled" | "disabled";
    /** 计费通道：plan=Agent Plan 专属通道，metered=按量付费，custom=自定义端点 */
    llm_channel: "plan" | "metered" | "custom";
    llm_temperature: number;
    llm_timeout_seconds: number;
    llm_enabled: boolean;
    llm_has_api_key: boolean;
    llm_fallback_has_api_key: boolean;
    // 平台连接
    platforms: Record<string, { enabled: boolean }>;
    /** R8：各阶段独立超时（小时），可在线改 */
    timeouts: Record<string, number | string>;
    /** R11 三模式权重 + R12 半衰期（可在线调，每个模式合计必须 100） */
    scoring: {
      cold_start: Record<string, number>;
      sample_accumulating: Record<string, number>;
      stable_operation: Record<string, number>;
      timeliness_half_life_days: Record<string, number>;
      active_dimension_min_uses: number;
      active_dimension_window_days: number;
      shrinkage_m: number;
    };
    /** 节奏随机间隔（秒）：基准 ± jitter */
    rhythm_intervals: Record<string, number>;
    /** R22 红线词库（词库可增删，门禁开关仍硬锁） */
    redline_keywords: string[];
    /** 通知通道（webhook URL 只在 .env，这里只回是否已配置） */
    notification_channels: Record<string, {
      enabled: boolean;
      has_webhook_url: boolean;
      available?: boolean;
    }>;
  };
  overrides_file: string;
  overrides: Record<string, unknown>;
  audit: { id: number; key: string; old_value: string | null; new_value: string | null; changed_at: string | null }[];
}

/** 访问保护（P0）：未设置口令时全站可访问但显示警告 */
export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
  needs_login: boolean;
}

/** 选题库来源（上传 / 远程 / 服务器路径） */
export interface KbSource {
  id: string;
  name: string;
  kind: "file" | "server" | "remote";
  format: string;
  display_path: string;
  enabled: boolean;
  rows?: number | null;
  field_mapping?: Record<string, string>;
  header_keys?: string[];
  url?: string;
  last_sync_at?: string | null;
  last_error?: string | null;
}

export interface KbPreview {
  id: string;
  format: string;
  columns: string[];
  mapping: Record<string, string>;
  missing: string[];
  rows: number;
  sample: Record<string, string>[];
  display_path: string;
}

/** 行业模板：一键填画像 / 维度 / 合规红线 */
export interface IndustryPreset {
  key: string;
  is_builtin?: boolean;
  version?: number;
  is_active?: boolean;
  archived_at?: string | null;
  name: string;
  description: string;
  positioning: string;
  persona_keywords: string[];
  forbidden_directions: string[];
  extra_redlines: string[];
  dimensions: string[];
  platform_notes: Record<string, string>;
}

export interface HotSource {
  name: string;
  type: "aggregate" | "rss" | "json";
  url: string;
  platforms?: string[];
  platform?: string;
  enabled?: boolean;
  last_test?: {
    status: "success" | "failed" | "empty";
    item_count: number;
    sample_titles: string[];
    message: string;
    tested_at: string;
  } | null;
}

export interface KbInspect {
  path: string;
  file_type: string;
  table?: string;
  field_mapping: Record<string, string>;
  columns: string[];
  missing_fields: string[];
  stats: { total?: number; unique_dimensions?: number };
  ok: boolean;
  error?: string;
}

// ===== G-09 母计划 / 内容编辑 / 生产线 =====
export interface PlatformPlan {
  /** 本平台每轮篇数（≤ 母计划每平台选题数；不填 = 跟随母计划） */
  topic_count?: number;
  /** 自动排期的发布时段 HH:MM */
  publish_slots?: string[];
}

export interface PlanCadence {
  round_interval_days?: number;
  rounds_planned?: number;
  goal_id?: string;
}

export interface MasterPlan {
  topic_count_per_platform: number;
  dimension_allocation: Record<string, number>;
  enabled_platforms: string[];
  content_format: string;
  per_platform?: Record<string, PlatformPlan>;
  cadence?: PlanCadence;
}

export interface PlanSuggestion {
  goal_id: string;
  suggestion: { topic_count_per_platform: number; enabled_platforms: string[]; cadence: PlanCadence };
  notes: string[];
}

export interface PoolTopic {
  id: string;
  title: string;
  dimension: string;
  summary: string;
  link: string;
  date: string | null;
  source: string;
  source_kind: "kb" | "hot" | "manual";
  content_count: number;
  excluded: boolean;
  excluded_at: string | null;
  excluded_reason: string | null;
}

export interface TopicPoolResponse {
  items: PoolTopic[];
  total: number;
  dimensions: string[];
  report: { kb_count: number; extra_count: number; excluded_count: number; kb_error: string | null; file_type: string | null };
}

export type StepState = "success" | "failed" | "skipped" | "pending";

export interface OrchestrationStep {
  id: string;
  enabled: boolean;
  label: string;
  skill: string;
  locked: boolean;
  requires: string[];
  desc: string;
  inputs: string[];
}

export interface OrchestrationView {
  steps: OrchestrationStep[];
  updated_at: string | null;
  default: { id: string; enabled: boolean }[];
}

export interface OrchestrationRun {
  round_id: number;
  round_number: number;
  status: string;
  trigger_source: string;
  created_at: string;
  duration_seconds: number | null;
  step_status: Record<string, { status: StepState; detail: string }>;
  errors: string[];
  has_report: boolean;
}

export interface DiagnoseItem {
  no: number;
  name: string;
  status: "pass" | "warn" | "block";
  message: string;
  fix_page: string | null;
  fix_tab: string | null;
}

export interface DiagnoseResult {
  overall: "pass" | "warn" | "block";
  items: DiagnoseItem[];
  checked_at: string;
}

export interface DeleteContentsResult {
  deleted: string[];
  blocked: { id: string; reason: string }[];
  not_found: string[];
  platform_drafts_left: number;
  files_removed: number;
}

export interface AutogrowStatus {
  config: {
    enabled: boolean; apply_feedback: boolean; topic_count: number; cooldown_hours: number;
    max_rounds_per_day: number; max_consecutive_failures: number; stop_when_goals_achieved: boolean;
    feedback_max_bias: number; feedback_lookback_rounds: number;
  };
  job_enabled: boolean;
  next_check_at: string | null;
  verdict: { can_start: boolean; action: string; reason: string; next_eligible_at?: string };
  feedback: {
    source_round_ids: number[];
    dimension_bias: Record<string, number>;
    bias_reasons: Record<string, string[]>;
    copy_hints: Record<string, string[]>;
    dropped_hints: string[];
  };
  recent_runs: { id: number; ran_at: string; action: string; reason: string; round_id: number | null }[];
}

export interface PlanSignatureHistory {
  plan_signature: string;
  plan_snapshot: MasterPlan | string;
  round_id: number | null;
  round_number: number | null;
  created_at: string;
  author: string | null;
}

export interface PlanResponse {
  plan: MasterPlan;
  signature: string;
  updated_at: string | null;
  /** plan_settings=用户保存过 / derived=从最近一轮推导 */
  source: "plan_settings" | "derived" | string;
  per_platform: Record<Platform, {
    enabled: boolean; config_enabled: boolean; topic_count: number;
    topic_count_override: number | null; publish_slots: string[];
  }>;
  history: PlanSignatureHistory[];
  /** R18：签名与当前母计划不一致的活跃轮次 */
  stale_rounds: { id: number; round_number: number; status: string; plan_signature: string }[];
}

export interface SavePlanResult {
  signature: string;
  previous_signature: string;
  changed: boolean;
  updated_at: string;
  plan: MasterPlan;
  stale_rounds: number[];
}

export interface ContentItem {
  id: string;
  round_id: number | null;
  topic_id: string | null;
  platform: Platform;
  title: string;
  body: string;
  tags: string[];
  image_count: number;
  /** G-36：卡片图文件名（下载走 contentImageUrl，不下发绝对路径） */
  image_paths?: string[];
  word_count: number | null;
  card_style: string | null;
  guard_outcome: string | null;
  guard_reason: string | null;
  created_at: string;
  draft_id: string | null;
  draft_status: string | null;
  draft_error: string | null;
  draft_saved_at: string | null;
  /** 首次用户确认发布的时间；即使后续采集失败变为 failed，也仍属于不可变历史内容。 */
  draft_user_confirmed_at?: string | null;
  review_rejected: boolean;
  review_rejection_reason: string | null;
}

export interface RecheckResult {
  id: string;
  result: "pass" | "warn" | "fail" | "error";
  passes: boolean;
  reason: string;
  redline_hits: string[];
}

export interface PipelineStage {
  key: string;
  label: string;
  count: number;
  note: string;
}

export interface PipelinePlatformRow {
  platform: Platform;
  contents: number;
  guard_pass: number;
  cards: number;
  draft_saved: number;
  draft_pending: number;
  draft_failed: number;
}

export interface PipelineResponse {
  round_id: number;
  round_number: number;
  status: string;
  stages: PipelineStage[];
  guard_rejections: number | null;
  drafts_by_status: Record<string, number>;
  per_platform: PipelinePlatformRow[];
}

// ===== 账号管理 =====
export interface AccountInfo {
  platform: Platform;
  platform_display: string;
  platform_icon?: string;
  /** logged_in | logging_in | not_logged_in */
  status: string;
  login_proc_state?: string;
  login_proc_message?: string;
  has_storage_state: boolean;
  enabled?: boolean;
  configured?: boolean;
  account_registered?: boolean;
  account_active?: boolean;
  credential_present?: boolean;
  circuit_broken?: boolean;
  preflight_ready?: boolean;
  blockers?: string[];
  /** 扫码中的 QR（data:image/png;base64,xxx） */
  qr_base64?: string | null;
}

export interface RegisteredPlatformAccount {
  id: number;
  platform: Platform;
  account_handle: string;
  display_name: string;
  status: "active" | "inactive";
  notes: string;
  created_at?: string;
  updated_at?: string;
}

export interface AccountIdentity {
  platform: Platform;
  logged_in: boolean;
  name: string;
  avatar_url: string | null;
  error: string | null;
}

export interface LoginStatus {
  platform: Platform;
  platform_display: string;
  /** starting | qr_ready | scanned | sms_required | verifying | success | expired | error | unknown */
  state: string;
  message: string;
  qr_path?: string | null;
  /** data:image/png;base64,xxx */
  qr_base64?: string | null;
  /** 二维码文件的修改时间（ms），换码时变化 */
  qr_ts?: number;
  proc_alive?: boolean;
  has_storage_state?: boolean;
  ts: number;
}
export interface AiCallRecord {
  id: number;
  step: string;
  module?: string | null;
  model: string;
  prompt_version: string;
  algo_version?: string | null;
  input_digest?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms: number;
  duration_ms?: number | null;
  output_valid?: number | null;
  success: number;
  attempt?: number;
  called_at: string;
}

export interface UserOverrideRecord {
  id: number;
  round_id?: number | null;
  round_number?: number | null;
  ai_call_id?: number | null;
  override_field: string | null;
  original_value: string | null;
  override_value: string | null;
  reason: string;
  overridden_by: string;
  overridden_at: string;
}

export interface RedlineRecord {
  id: string;
  round_id: number | null;
  round_number: number | null;
  platform: Platform;
  title: string;
  guard_outcome: "warn" | "fail" | "error" | string;
  guard_reason: string | null;
  created_at: string;
}

// ===== 运营控制台补充类型 =====
export interface OpsKpiResponse {
  today_ops: Record<string, Record<string, number>>;
  draft_status: Record<string, number>;
  circuit_breaker_active: { platform: Platform; reason: string; detected_at: string; cooldown_until: string | null }[];
  platform_stats: Record<Platform, { rounds: number; drafts_saved: number; drafts_published: number; circuit_broken: boolean }>;
  /** 后端回显的展示窗口（1/7/30/90），today_ops 实际是按该窗口聚合 */
  window_days?: number;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface OpsLogItem {
  id: number;
  round_id: number | null;
  platform: Platform;
  action_type: "passive_reply" | "active_comment" | "follow" | "browse";
  target_url: string | null;
  target_author: string | null;
  content: string | null;
  schedule_time: string | null;
  executed_at: string | null;
  status: "success" | "failed" | "skipped" | "circuit_broken" | "pending_verify";
  error_msg: string | null;
  resolved_at?: string | null;
  resolved_by?: string | null;
  resolution_action?: "sent" | "not_sent" | null;
  resolution_note?: string | null;
  rhythm_wait_seconds: number | null;
}

export interface TaskRunItem {
  id: number;
  task_type: string;
  biz_id: string | null;
  platform: Platform | null;
  status: "pending" | "running" | "interrupted" | "success" | "failed" | "skipped" | "pending_verify";
  attempt: number;
  max_attempts: number;
  started_at: string | null;
  finished_at: string | null;
  next_run_at: string | null;
  error_msg: string | null;
  created_at: string;
  resolved_at?: string | null;
  resolved_by?: string | null;
  resolution_action?: "sent" | "not_sent" | null;
}

export interface NotificationItem {
  id: number;
  level: "info" | "warning" | "error" | "critical";
  type: string;
  platform: Platform | null;
  title: string;
  body: string | null;
  channel: string | null;
  sent_at: string;
  read_at: string | null;
  target_page?: string;
  target_tab?: string | null;
  target_type?: "task_run" | "ops_log" | "round" | "draft" | null;
  target_id?: number | string | null;
}

export interface SchedulerJob {
  id: string;
  /** 任务说明（来自后端配置） */
  name: string;
  enabled: boolean;
  /** interval_minutes 与 cron 二选一 */
  interval_minutes: number | null;
  cron: string | null;
  /** 人类可读的排期（「每 120 分钟」/「cron 0 8 * * *」） */
  schedule: string;
  next_run_time: string | null;
  trigger: string | null;
  /** false = 动态注册的一次性任务（data_fetch_*），不可配置 */
  configurable: boolean;
}

/** 通知规则（notify_rules 表）：有规则的场景按规则限定通道/级别，无规则走 config 默认 */
export interface NotifyRule {
  id: number;
  scenario: string;
  channel: string;
  level: string;
  enabled: number | boolean;
  condition?: string | null;
  notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** 平台文案风格（platform_styles 表）：文案生成时注入 prompt + 标签兜底 */
export interface PlatformStyle {
  platform: Platform;
  title_style: string;
  body_style: string;
  tone: string;
  /** 不带 # 前缀；空数组 = 不加平台通用标签，只留选题维度标签 */
  hashtags: string[];
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface OpsTargetsView {
  [platform: string]: {
    active_comments: { url: string; content: string }[];
    follows: string[];
  };
}

export interface RhythmConfig {
  enabled_platforms: Platform[];
  quotas: Record<Platform, { passive_replies: number; active_comments: number; follows: number }>;
  posting_windows: Record<Platform, string[]>;
  /** 运营时段窗口（所有平台共用，可在页面直接改） */
  ops_window: { start_hour: number; end_hour: number } | null;
  /** 随机操作间隔（基准 ± jitter，秒） */
  intervals: Record<string, number>;
}

// ===== fetch wrapper =====
let activeTenantId: string | null = null;
let tenantContextInitialization: Promise<void> | null = null;
// vite import.meta.env.BASE_URL 在 base: "/iterwave/" 时为 "/iterwave/"
// 本地 vite dev 时 BASE_URL 为 "/"，去掉尾部斜杠 → ""，等价于原来的空字符串
const API = import.meta.env.BASE_URL?.replace(/\/$/, "") ?? "";

async function ensureTenantContext(): Promise<void> {
  if (activeTenantId) return;
  if (!tenantContextInitialization) {
    tenantContextInitialization = (async () => {
      // 不通过 fetchJson，避免初始化请求递归；该 GET 不需要账号组请求头。
      const response = await fetch(API + "/api/tenants", {
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok) {
        throw new Error("账号组初始化失败，请刷新页面后重试");
      }
      const result = await response.json() as TenantListResponse;
      activeTenantId = result.runtime_tenant_id || result.selected_tenant_id || null;
      if (!activeTenantId) {
        throw new Error("未能确定当前运行账号组，请刷新页面后重试");
      }
    })().finally(() => {
      tenantContextInitialization = null;
    });
  }
  await tenantContextInitialization;
}

/**
 * 错误提示透传规则：后端写好的中文业务提示（包括 5xx，如「图片生成服务暂时不可用…」）原样展示；
 * 仅当 detail 为空，或含堆栈、源码/绝对路径、URL、token/密钥、驱动异常类名等内部特征时，
 * 才替换为通用提示，避免内部诊断信息泄露到浏览器。
 */
const INTERNAL_DETAIL_PATTERNS: RegExp[] = [
  /\btraceback\b/i,
  /File ["'][^"']+["'], line \d+/i,
  /\.(?:py|pyc|db|sqlite3?|ya?ml|env|log|ini|toml)\b(?::\d+)?/i,
  /(?:^|[\s"'(=:])\/(?:home|root|tmp|var|usr|opt|etc|srv|app|workspace|Users|private|data)\//,
  /\b[A-Za-z]:\\[^\s]/,
  /\b(?:https?|wss?|ftp|file|sqlite|postgres(?:ql)?|mysql|redis):\/\/\S+/i,
  /\b(?:bearer|authorization)\b\s*[:=]?\s*[A-Za-z0-9._~+/-]{8,}/i,
  /\b(?:api[_-]?key|access[_-]?token|secret|password|passwd|token)\s*[:=]\s*\S+/i,
  /\b(?:sk|ak|ghp|gho|xox[abp])-[A-Za-z0-9_-]{8,}/,
  /\b[A-Za-z0-9_]{40,}\b/,
  /\b[A-Z][A-Z0-9]*_(?:API_KEY|KEY|TOKEN|SECRET|PASSWORD|URL)\b/,
  /\b(?:sqlite3|sqlalchemy|psycopg2?|pymysql)\.|\b(?:OperationalError|ProgrammingError|IntegrityError|KeyError|TypeError|ValueError|AttributeError|RuntimeError|Exception)\b/,
  /constraint failed|no such (?:table|column)|database is locked/i,
  /<\/?(?:html|body|head|title)\b/i,
];

/** 把后端错误 detail 转成可展示给用户的提示（导出以便单测/复用）。 */
export function clientErrorMessage(status: number, rawDetail: string | null | undefined): string {
  const detail = (rawDetail ?? "").trim();
  const generic = status >= 500
    ? "服务暂时不可用，请稍后重试"
    : "操作失败，内部诊断信息已记录，请检查配置或联系管理员";
  if (!detail) return status >= 500 ? generic : "请求失败，请稍后重试";
  if (INTERNAL_DETAIL_PATTERNS.some((re) => re.test(detail))) return generic;
  return detail;
}

async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method || "GET").toUpperCase();
  if (method !== "GET" && !path.startsWith("/api/auth/")) {
    await ensureTenantContext();
  }
  let res: Response;
  try {
    const headers = new Headers(options?.headers);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (activeTenantId) headers.set("X-Tenant-Id", activeTenantId);
    res = await fetch(API + path, { ...options, headers });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("网络连接失败，请检查服务是否运行及网络状态后重试");
    }
    throw error;
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let detail = "";
    let errorCode = "";
    let parsedJson = false;
    try {
      const parsed = JSON.parse(text);
      parsedJson = true;
      const payload = parsed?.detail ?? parsed;
      if (typeof payload === "string") {
        detail = payload;
        // FastAPI 常见结构：detail 是字符串，业务错误码放在响应顶层。
        errorCode = String(parsed?.code || "");
      } else if (payload && typeof payload === "object") {
        detail = String(payload.message || payload.detail || "");
        errorCode = String(payload.code || parsed?.code || "");
      }
    } catch {
      // 非 JSON 错误体（如网关 HTML 页、纯文本 Internal Server Error）
    }
    // 5xx 的非 JSON 响应体来自网关/框架默认页，不是后端业务提示，按“无 detail”处理；
    // 4xx 的非 JSON 文本保留原文，但仍要过内部特征检查。
    if (!parsedJson && res.status < 500) detail = text || res.statusText || "";
    detail = clientErrorMessage(res.status, detail);
    const error = new Error(detail) as Error & { status?: number; code?: string };
    error.status = res.status;
    error.code = errorCode;
    if (res.status === 409 && errorCode === "TENANT_CONTEXT_CHANGED") {
      // 页面展示的数据属于旧账号组；不得继续留在旧页面上操作新运行空间。
      if (typeof window !== "undefined") window.location.reload();
    }
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ===== API Functions =====
export interface ActivationStatus {
  tenant_id: string;
  latest_sample_run: { id: string; status: string } | null;
  model_mode: "byok" | "hosted_trial";
  model_ready: boolean;
  model_message: string;
  quick_experience_available: boolean;
  platform_metric_fields: Record<Platform, string[]>;
  activation_limits: {
    account_description_min_chars: number; account_description_max_chars: number;
    sample_max_output_tokens: number;
    max_model_calls_per_run: number; max_total_output_tokens_per_run: number;
    daily_runs_used: number;
  };
  cohort_started_at: string;
}
export interface ActivationSample {
  id: string; platform: Platform; angle: string; title: string; body: string;
  hashtags: string[]; quality_status: "pass" | "rejected";
  quality_findings: string[]; adopted_at: string | null; rejected_at: string | null;
  rejection_reason: string | null;
}
export interface ActivationSavedSample {
  id: string; sample_run_id: string; platform: Platform; angle: string; title: string; body: string;
  hashtags: string[]; adopted_at: string; created_at: string; account_description: string;
  promoted_content_id?: string | null; promoted_at?: string | null;
}
export interface ActivationSampleRun {
  run: {
    id: string; platform: Platform; account_description: string; status: string;
    model_mode: string; error_code: string | null; error_message_safe: string | null;
    created_at: string; started_at: string | null; completed_at: string | null; attempt_count?: number;
  };
  contents: ActivationSample[];
}
export interface WeeklyReport {
  period: { start: string; end: string };
  best_content: { platform: Platform; metric: string; value: number; content_id: string;
    metric_snapshot_id: string | number; title: string; measured_at: string; source: string; conflict?: boolean }[];
  why_it_may_work: { statement: string; sample_size: number; evidence_content_ids: string[];
    metric_snapshot_ids?: (string | number)[]; platform?: Platform; time_window?: { start: string; end: string };
    features?: Record<string, number | string | null>; examples?: string[] }[];
  next_actions: { action: string; basis: string; verification: string; evidence_content_ids: string[];
    metric_snapshot_ids?: (string | number)[]; platform?: Platform; time_window?: { start: string; end: string };
    sample_size: number; suggestion_key: string; decision: "pending" | "adopted" | "ignored";
    outcome: "pending" | "success" | "no_effect" | "unclear"; outcome_note?: string | null;
    decided_at?: string | null; outcome_recorded_at?: string | null }[];
  sample_size: number; effective_review_count?: number; data_sources: string[]; limitations: string[]; generated_at: string;
  data_conflicts?: { platform: Platform; content_id: string; metrics: string[]; selected_sources: Record<string, string> }[];
}

export const api = {
  status: () => fetchJson<SystemStatus>("/api/status"),
  goals: () => fetchJson<Goal[]>("/api/goals"),
  /** 总体目标进度（后端统一计算等权平均，前端只展示） */
  goalsSummary: () => fetchJson<GoalSummary>("/api/goals/summary"),
  personas: () => fetchJson<PersonasView>("/api/personas"),
  accountSnapshots: (platform?: string) =>
    fetchJson<{ id: number; platform: Platform; followers: number | null; likes_total: number | null; captured_at: string; source: string }[]>(
      `/api/account_snapshots${platform ? `?platform=${platform}` : ""}`),
  addAccountSnapshot: (data: { platform: Platform; followers?: number | null; likes_total?: number | null }) =>
    fetchJson<{ platform: string; followers: number | null; captured_at: string }>("/api/account_snapshots", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    }),
  setGoalManualValue: (goalId: string, current_value: number) =>
    fetchJson<Goal>(`/api/goals/${goalId}/manual_value`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_value }),
    }),
  createGoal: (data: Partial<Goal>) =>
    fetchJson<Goal>("/api/goals", {
      method: "POST", body: JSON.stringify(data),
    }),
  updateGoal: (goalId: string, data: Partial<Goal>) =>
    fetchJson<Goal>(`/api/goals/${goalId}`, {
      method: "PATCH", body: JSON.stringify(data),
    }),
  refreshGoalProgress: () =>
    fetchJson<{ updated: Record<string, number | null>; goals: Goal[]; summary: GoalSummary }>(
      "/api/goals/progress/refresh", { method: "POST" },
    ),
  taskQueue: (params: { scope?: "all" | "active" | "history" | "failed"; page?: number; pageSize?: number; taskType?: string; tenantId?: string; errorKeyword?: string; dateFrom?: string; dateTo?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.scope) query.set("scope", params.scope);
    if (params.page != null) query.set("page", String(params.page));
    if (params.pageSize != null) query.set("page_size", String(params.pageSize));
    if (params.taskType) query.set("task_type", params.taskType);
    if (params.tenantId) query.set("tenant_id", params.tenantId);
    if (params.errorKeyword) query.set("error_keyword", params.errorKeyword);
    if (params.dateFrom) query.set("date_from", params.dateFrom);
    if (params.dateTo) query.set("date_to", params.dateTo);
    const encoded = query.toString();
    const suffix = encoded ? `?${encoded}` : "";
    return fetchJson<GlobalTaskQueueResponse>(`/api/task-queue${suffix}`);
  },
  cancelTask: (taskId: string) =>
    fetchJson<{ task_id: string; status: string }>(`/api/task-queue/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    }),
  resolveTask: (taskId: string, input: { action: "handled" | "ignored"; note: string }) =>
    fetchJson<{
      task_id: string; action: "handled" | "ignored"; actor: string;
      note: string; acted_at: string; created: boolean;
    }>(`/api/task-queue/${encodeURIComponent(taskId)}/resolve`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  retryTask: (taskId: string, input: { confirmedNotExecuted: boolean; confirmationNote: string }) =>
    fetchJson<{
      task_id: string; retry_of_task_id: string; tenant_id: string; task_type: string;
      status: string; created: boolean; confirmed_by: string; confirmation_note: string;
    }>(`/api/task-queue/${encodeURIComponent(taskId)}/retry`, {
      method: "POST",
      body: JSON.stringify({
        confirmed_not_executed: input.confirmedNotExecuted,
        confirmation_note: input.confirmationNote,
      }),
    }),
  rounds: (status?: string) =>
    fetchJson<SpiralRound[]>(status ? `/api/rounds?status=${status}` : "/api/rounds"),
  roundsPage: (page: number, pageSize = 20, status?: string) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (status) params.set("status", status);
    return fetchJson<SpiralRoundPage>(`/api/rounds?${params.toString()}`);
  },
  round: (id: number) => fetchJson<SpiralRound & { drafts: Draft[] }>(`/api/rounds/${id}`),
  createRound: (data: { topic_count?: number; platforms?: Platform[]; goal_id?: string }) =>
    fetchJson<{ id: number; round_number: number; status: string }>("/api/rounds", {
      method: "POST", body: JSON.stringify(data),
    }),
  runRound: (id: number, data: { topic_count?: number; platforms?: Platform[]; save_drafts?: boolean; template_id?: number } = {}) =>
    fetchJson<QueueTaskSubmission>(`/api/rounds/${id}/run`, {
      method: "POST", body: JSON.stringify(data),
    }),
  roundRunStatus: (id: number) =>
    fetchJson<{ round_id: number; task_id?: string; status?: GlobalTaskQueueItem["status"]; queued?: boolean; running: boolean; report: RunRoundReport | null; error: string | null; error_code?: string | null }>(
      `/api/rounds/${id}/run_status`,
    ),
  /** 恢复已生成、尚未存平台草稿的内容；不会重新生成或发布。 */
  savePendingDrafts: (id: number) =>
    fetchJson<QueueTaskSubmission>(`/api/rounds/${id}/save_pending`, {
      method: "POST",
    }),
  transitionRound: (id: number, target_status: string, reason = "") =>
    fetchJson<{ round_id: number; new_status: string }>(`/api/rounds/${id}/transition`, {
      method: "POST", body: JSON.stringify({ target_status, reason }),
    }),
  drafts: (status?: string, platform?: Platform) => {
    const p = new URLSearchParams();
    if (status) p.set("status", status);
    if (platform) p.set("platform", platform);
    return fetchJson<Draft[]>(`/api/drafts?${p}`);
  },
  draftById: async (id: string) => {
    const p = new URLSearchParams({ draft_id: id });
    const rows = await fetchJson<Draft[]>(`/api/drafts?${p}`);
    return rows[0] ?? null;
  },
  draftsPage: (status: string | undefined, platform: Platform | undefined, page: number, pageSize = 20) => {
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (status) p.set("status", status);
    if (platform) p.set("platform", platform);
    return fetchJson<DraftPage>(`/api/drafts?${p}`);
  },
  draftsAll: async (status?: string, platform?: Platform): Promise<Draft[]> => {
    const first = await api.draftsPage(status, platform, 1, 100);
    const all = [...first.items];
    for (let start = 2; start <= first.pages; start += 5) {
      const end = Math.min(first.pages, start + 4);
      const batch = await Promise.all(
        Array.from({ length: end - start + 1 }, (_, index) =>
          api.draftsPage(status, platform, start + index, 100),
        ),
      );
      all.push(...batch.flatMap((result) => result.items));
    }
    return all;
  },
  /** noteUrl：已发布笔记链接（可选；不填则该篇跳过被动评论轮询） */
  confirmPublish: (
    draftId: string,
    noteUrl?: string,
    qualityOverrideReason?: string,
    platformSyncOverrideReason?: string,
  ) =>
    fetchJson<{ draft_id: string; status: string; note_url?: string | null; already_confirmed?: boolean }>(
      `/api/drafts/${draftId}/publish_confirm`,
      {
        method: "POST",
        body: JSON.stringify({
          note_url: noteUrl ?? null,
          quality_override_reason: qualityOverrideReason?.trim() || null,
          platform_sync_override_reason: platformSyncOverrideReason?.trim() || null,
        }),
      },
    ),
  verifyDraftSave: (draftId: string, saved: boolean, note: string) =>
    fetchJson<{ draft_id: string; status: string; message: string }>(
      `/api/drafts/${draftId}/verify_save`,
      { method: "POST", body: JSON.stringify({ saved, note: note.trim() }) },
    ),
  confirmPlatformSync: (draftId: string, reason: string) =>
    fetchJson<{ draft_id: string; platform_content_sync: string; message: string }>(
      `/api/drafts/${draftId}/confirm_platform_sync`,
      { method: "POST", body: JSON.stringify({ reason: reason.trim() }) },
    ),
  /** 「✕ 不发了」：draft_pending / draft_saved → cancelled */
  cancelDraft: (draftId: string) =>
    fetchJson<{ draft_id: string; status: string }>(`/api/drafts/${draftId}/cancel`, {
      method: "POST",
    }),
  scoreTopics: (limit = 20) =>
    fetchJson<{ mode: string; topics: ScoredTopic[] }>(`/api/topics/score?limit=${limit}`),
  /** 触发后台采集 + 标签匹配（立即返回，202） */
  refreshHotTopics: () =>
    fetchJson<HotRefreshJob & { started: boolean }>("/api/hot_topics/refresh", { method: "POST" }),
  /** 触发后台大模型研判（立即返回，202），只研判当前榜单 */
  researchHotTopics: () =>
    fetchJson<HotResearchJob & { started: boolean }>("/api/hot_topics/research", { method: "POST" }),
  hotTopics: (refresh = false, options?: {
    page?: number; pageSize?: number; tab?: "all" | "matched" | "urgent" | "unknown";
    source?: string; keyword?: string; sortBy?: "heat" | "match";
  }) => {
    const params = new URLSearchParams();
    if (refresh) params.set("refresh", "true");
    if (options?.page != null) params.set("page", String(options.page));
    if (options?.pageSize != null) params.set("page_size", String(options.pageSize));
    if (options?.tab) params.set("tab", options.tab);
    if (options?.source) params.set("source", options.source);
    if (options?.keyword) params.set("keyword", options.keyword);
    if (options?.sortBy) params.set("sort_by", options.sortBy);
    const query = params.toString();
    return fetchJson<HotTopicsResponse>(`/api/hot_topics${query ? `?${query}` : ""}`);
  },
  gaps: (roundId?: number) =>
    fetchJson<SpiralAnalysis>(roundId ? `/api/gaps?round_id=${roundId}` : "/api/gaps"),
  /** 复盘洞察（螺旋引擎输出，每平台纵深分析 + 结论） */
  insights: () =>
    fetchJson<{ items: InsightItem[] }>("/api/insights"),
  /** 四平台 draft 真实分组（Dashboard 右侧进度卡用） */
  platformProgress: () =>
    fetchJson<Record<Platform, PlatformProgress>>("/api/platform_progress"),
  /** 待处理/阻塞事项（Dashboard KPI + 待我处理列表用） */
  actionItems: () =>
    fetchJson<{ items: ActionItem[]; total: number }>("/api/action_items"),
  /** 复盘「采纳建议」：把缺口维度写回母计划（后端触发 R18 下游失效） */
  adoptGaps: (dimensions: string[]) =>
    fetchJson<{ signature: string; adopted_dimensions: string[]; stale_rounds: number[] }>(
      "/api/plan_adjustments/adopt",
      { method: "POST", body: JSON.stringify({ dimensions }) },
    ),
  /** 热点 → 选题库（R22：必须有 source + 原文链接） */
  importHotTopic: (payload: { title: string; source: string; url: string; dimension?: string; confirmed_by?: string }) =>
    fetchJson<{ id: string; imported: boolean; message?: string; manual_confirmation_recorded?: boolean }>("/api/hot_topics/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  /** 正式运营设置向导进度（按账号组存后端 app_settings） */
  onboardingProgress: () =>
    fetchJson<OnboardingProgress>("/api/onboarding/progress"),
  saveOnboardingProgress: (step_key: string, step_index: number) =>
    fetchJson<OnboardingProgress>("/api/onboarding/progress", {
      method: "PUT",
      body: JSON.stringify({ step_key, step_index }),
    }),
  businessAnalyticsSummary: (days = 30) =>
    fetchJson<BusinessAnalyticsSummary>(`/api/analytics/business-summary?days=${days}`),
  metrics: (platform?: Platform, page?: number, pageSize = 20) => {
    const params = new URLSearchParams();
    if (platform) params.set("platform", platform);
    if (page != null) {
      params.set("page", String(page));
      params.set("page_size", String(pageSize));
    }
    const query = params.toString();
    return fetchJson<MetricsResponse>(`/api/metrics${query ? `?${query}` : ""}`);
  },
  circuitBreaker: () => fetchJson<{ events: CircuitBreakerEvent[]; broken_platforms: Platform[] }>("/api/circuit_breaker"),
  recoverCb: (platform: Platform) =>
    fetchJson<any>(`/api/circuit_breaker/recover?platform=${platform}`, { method: "POST" }),
  resetCb: (platform: Platform) =>
    fetchJson<{ reset_count: number }>(`/api/circuit_breaker/reset?platform=${platform}`, { method: "POST" }),
  schedule: (startDate?: string, endDate?: string) => {
    const p = new URLSearchParams();
    if (startDate) p.set("start_date", startDate);
    if (endDate) p.set("end_date", endDate);
    return fetchJson<ScheduleResponse>(`/api/schedule?${p}`);
  },
  // === G-13 排期写入口 ===
  scheduleDraft: (draft_id: string, scheduled_publish_at: string) =>
    fetchJson<{ draft_id: string; scheduled_publish_at: string | null; deduped?: boolean }>(
      "/api/schedule", { method: "POST", body: JSON.stringify({ draft_id, scheduled_publish_at }) },
    ),
  /** scheduled = null → 取消排期 */
  updateSchedule: (draft_id: string, scheduled: string | null) =>
    fetchJson<{ draft_id: string; scheduled_publish_at: string | null }>(
      `/api/schedule/${draft_id}`, { method: "PATCH", body: JSON.stringify({ scheduled_publish_at: scheduled }) },
    ),
  contentPlans: (startDate?: string, endDate?: string) => {
    const p = new URLSearchParams();
    if (startDate) p.set("start_date", startDate);
    if (endDate) p.set("end_date", endDate);
    return fetchJson<{ items: ContentPlan[]; total: number }>(`/api/content-plans?${p}`);
  },
  tenantSkills: (enabled?: boolean) => fetchJson<{ items: TenantSkillManifest[]; total: number; builtin_items: BuiltinSkillManifest[]; builtin_total: number; execution_enabled: false; custom_execution_enabled: false }>(`/api/skills${enabled === undefined ? "" : `?enabled=${enabled}`}`),
  registerTenantSkill: (data: Pick<TenantSkillManifest, "skill_id" | "name" | "version" | "description" | "category" | "inputs" | "outputs" | "permissions">) =>
    fetchJson<TenantSkillManifest & { execution_enabled: false }>("/api/skills", { method: "POST", body: JSON.stringify(data) }),
  setTenantSkillEnabled: (skill_id: string, enabled: boolean, manifest_version: number) =>
    fetchJson<{ skill_id: string; enabled: boolean; manifest_version: number; execution_enabled: false }>(
      `/api/skills/${skill_id}`, { method: "PATCH", body: JSON.stringify({ enabled, manifest_version }) },
    ),
  updateTenantSkill: (skill_id: string, data: Partial<Pick<TenantSkillManifest, "name" | "version" | "description" | "category" | "inputs" | "outputs" | "permissions" | "enabled">> & { manifest_version: number }) =>
    fetchJson<TenantSkillManifest & { execution_enabled: false }>(
      `/api/skills/${skill_id}`, { method: "PATCH", body: JSON.stringify(data) },
    ),
  deleteTenantSkill: (skill_id: string) =>
    fetchJson<{ skill_id: string; deleted: boolean }>(`/api/skills/${skill_id}`, { method: "DELETE" }),
  contentPlan: (id: string) => fetchJson<ContentPlan & { drafts: Array<{ draft_id: string; platform: Platform; status: string; title: string; round_id: number | null; round_number: number | null }>; rounds: Array<{ round_id: number; round_number: number | null; task_id: string | null; created_at: string }> }>(`/api/content-plans/${id}`),
  linkContentPlanDrafts: (id: string, draft_ids: string[]) =>
    fetchJson<{ plan_id: string; linked_now: string[]; linked_count: number; status: string }>(
      `/api/content-plans/${id}/drafts`, { method: "POST", body: JSON.stringify({ draft_ids }) },
    ),
  createContentPlan: (data: Pick<ContentPlan, "title" | "topic" | "platforms" | "planned_count" | "scheduled_date" | "notes">) =>
    fetchJson<ContentPlan>("/api/content-plans", { method: "POST", body: JSON.stringify(data) }),
  updateContentPlan: (id: string, data: Partial<Pick<ContentPlan, "title" | "topic" | "platforms" | "planned_count" | "scheduled_date" | "status" | "notes">> & { version: number }) =>
    fetchJson<ContentPlan>(`/api/content-plans/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  cancelContentPlan: (id: string, version: number) =>
    fetchJson<{ id: string; status: string }>(`/api/content-plans/${id}?version=${version}`, { method: "DELETE" }),
  archiveContentPlan: (id: string, version: number) =>
    fetchJson<{ id: string; status: ContentPlan["status"]; version: number }>(`/api/content-plans/${id}/archive?version=${version}`, { method: "POST" }),
  restoreContentPlan: (id: string, version: number) =>
    fetchJson<{ id: string; status: ContentPlan["status"]; version: number }>(`/api/content-plans/${id}/restore?version=${version}`, { method: "POST" }),
  startContentPlan: (id: string) =>
    fetchJson<{ plan_id: string; round_id: number; task_id: string; status: string; queued: boolean }>(
      `/api/content-plans/${id}/start`, { method: "POST" },
    ),
  globalOperationsSummary: (days = 30) => fetchJson<GlobalMonitorResponse>(`/api/global-monitor?days=${days}`),
  todos: (page = 1, pageSize = 20, tenantId?: string, source?: GlobalTodoItem["source"]) => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (tenantId) params.set("tenant_id", tenantId);
    if (source) params.set("source", source);
    return fetchJson<GlobalTodoResponse>(`/api/todos?${params.toString()}`);
  },
  // === 账号组隔离 ===
  tenants: async () => {
    const result = await fetchJson<TenantListResponse>("/api/tenants");
    const runtimeTenantId = result.runtime_tenant_id || result.selected_tenant_id || null;
    if (activeTenantId === null) {
      // 仅首次加载建立页面租户身份；后续发现服务端已切换，必须刷新整页，
      // 不能静默把旧页面的数据上下文改成新账号组。
      activeTenantId = runtimeTenantId;
    } else if (runtimeTenantId && runtimeTenantId !== activeTenantId) {
      if (typeof window !== "undefined") window.location.reload();
    }
    return result;
  },
  createTenant: (data: { tenant_id: string; name: string }) =>
    fetchJson<{ tenant: TenantItem; restart_required: boolean }>("/api/tenants", {
      method: "POST", body: JSON.stringify(data),
    }),
  createTenantBackup: () =>
    fetchJson<{ backup_name: string; size_bytes: number; created_at: string; includes: string[]; excludes: string[] }>(
      "/api/tenant-backup", { method: "POST" },
    ),
  tenantBackups: () => fetchJson<{ items: TenantBackupItem[] }>("/api/tenant-backups"),
  stageTenantBackupRestore: (backup_name: string, confirm: boolean) =>
    fetchJson<{ staged: boolean; restart_required: boolean; backup_name: string; message: string }>(
      "/api/tenant-backup/restore", { method: "POST", body: JSON.stringify({ backup_name, confirm }) },
    ),
  selectTenant: async (tenant_id: string) => {
    const result = await fetchJson<{ selected: { id: string; name: string }; runtime_tenant_id: string; restart_required: boolean; switch_status?: "completed" | "unchanged"; message: string }>(
      "/api/tenants/select", { method: "POST", body: JSON.stringify({ tenant_id }) },
    );
    activeTenantId = result.runtime_tenant_id || activeTenantId;
    return result;
  },
  // === 图片素材库 ===
  mediaAssets: (
    query = "",
    status: "active" | "archived" | "all" = "active",
    reviewStatus: "pending_review" | "approved" | "rejected" | "all" = "all",
    page?: number,
    pageSize = 20,
  ) => {
    const params = new URLSearchParams({
      status,
      review_status: reviewStatus,
      ...(page ? { page: String(page), page_size: String(pageSize) } : { limit: "200" }),
    });
    if (query) params.set("query", query);
    return fetchJson<MediaAssetPage>(`/api/media?${params.toString()}`);
  },
  uploadMediaAsset: (data: { filename: string; title?: string; alt_text?: string; tags?: string[]; data_base64: string }) =>
    fetchJson<MediaAsset>("/api/media", { method: "POST", body: JSON.stringify(data) }),
  archiveMediaAsset: (id: number) =>
    fetchJson<{ id: number; status: string; already_archived: boolean }>(`/api/media/${id}/archive`, { method: "POST" }),
  restoreMediaAsset: (id: number) =>
    fetchJson<{ id: number; status: string; restored?: boolean; already_active?: boolean }>(`/api/media/${id}/restore`, { method: "POST" }),
  renameMediaAsset: (id: number, title: string) =>
    fetchJson<{ id: number; title: string }>(`/api/media/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteMediaAsset: (id: number) =>
    fetchJson<{ id: number; deleted: boolean; file_removed: boolean }>(`/api/media/${id}`, { method: "DELETE" }),
  mediaUsageTargets: () => fetchJson<{ items: MediaUsageTarget[] }>("/api/media/targets"),
  recommendMediaAssets: (content_id: string, usage_role: "cover" | "illustration" | "background" = "cover") =>
    fetchJson<{ content_id: string; usage_role: string; method: "keyword_match" | "vision_model"; automatic_assignment: false; items: MediaRecommendation[]; message: string }>(
      `/api/media/recommendations?content_id=${encodeURIComponent(content_id)}&usage_role=${usage_role}`,
    ),
  useMediaAsset: (asset_id: number, content_id: string, usage_role: "cover" | "illustration" | "background" = "illustration") =>
    fetchJson<{ asset_id: number; content_id: string; platform: Platform; image_assigned: boolean; message: string }>(
      `/api/media/${asset_id}/use`, { method: "POST", body: JSON.stringify({ content_id, usage_role }) },
    ),
  mediaGenerationSettings: () => fetchJson<MediaGenerationSettings>("/api/media/generation-settings"),
  saveMediaGenerationSettings: (data: Pick<MediaGenerationSettings, "enabled" | "monthly_quota" | "prompt_prefix" | "model" | "size">) =>
    fetchJson<MediaGenerationSettings>("/api/media/generation-settings", { method: "PUT", body: JSON.stringify(data) }),
  generateMediaAsset: (data: { prompt: string; title?: string; alt_text?: string; tags?: string[] }) =>
    fetchJson<MediaAsset>("/api/media/generate", { method: "POST", body: JSON.stringify(data) }),
  generateMediaForContent: (content_id: string, usage_role: "cover" | "illustration" | "background" = "cover") =>
    fetchJson<MediaAsset>("/api/media/generate-for-content/" + encodeURIComponent(content_id) + "?usage_role=" + usage_role, { method: "POST" }),
  editMediaAsset: (id: number, data: { prompt: string; title?: string; alt_text?: string; tags?: string[] }) =>
    fetchJson<MediaAsset>(`/api/media/${id}/edit`, { method: "POST", body: JSON.stringify(data) }),
  reviewMediaAsset: (id: number, decision: "approved" | "rejected", note = "") =>
    fetchJson<{ id: number; review_status: string; review_note: string; previous_review_status: string }>(
      `/api/media/${id}/review`, { method: "POST", body: JSON.stringify({ decision, note }) },
    ),
  // === G-13 内容资产 / 字段映射 ===
  assets: (asset_type?: AssetType) =>
    fetchJson<{ items: AssetItem[]; total: number }>(
      `/api/assets${asset_type ? `?asset_type=${asset_type}` : ""}`,
    ),
  assetsPage: (asset_type: AssetType, page: number, pageSize = 20, includeInactive = false) => {
    const params = new URLSearchParams({ asset_type, page: String(page), page_size: String(pageSize) });
    if (includeInactive) params.set("include_inactive", "true");
    return fetchJson<AssetPage>(`/api/assets?${params.toString()}`);
  },
  createAsset: (data: { asset_type: AssetType; title: string; body?: string; source_url?: string; platform?: Platform }) =>
    fetchJson<AssetItem>("/api/assets", { method: "POST", body: JSON.stringify(data) }),
  updateAsset: (id: number, data: { asset_type: AssetType; title: string; body?: string; source_url?: string; platform?: Platform }) =>
    fetchJson<AssetItem>(`/api/assets/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  assetVersions: (id: number) => fetchJson<{ items: Array<{ version: number; title: string; body: string | null; created_at: string | null }> }>(`/api/assets/${id}/versions`),
  restoreAssetVersion: (id: number, version: number) => fetchJson<AssetItem>(`/api/assets/${id}/versions/${version}/restore`, { method: "POST" }),
  archiveAsset: (id: number) => fetchJson<{ id: number; is_active: boolean }>(`/api/assets/${id}/archive`, { method: "POST" }),
  restoreAsset: (id: number) => fetchJson<{ id: number; is_active: boolean }>(`/api/assets/${id}/restore`, { method: "POST" }),
  kbInspect: () => fetchJson<KbInspect>("/api/kb/inspect"),
  // === 访问保护 ===
  authStatus: () => fetchJson<AuthStatus>("/api/auth/status"),
  login: (password: string) =>
    fetchJson<AuthStatus>("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) }),
  authLogout: () => fetchJson<{ authenticated: boolean }>("/api/auth/logout", { method: "POST" }),
  setPassword: (password: string, bootstrapToken?: string) =>
    fetchJson<{ configured: boolean; message: string }>("/api/auth/password", {
      method: "POST", body: JSON.stringify({ password, bootstrap_token: bootstrapToken || "" }),
    }),
  // === 选题库三来源 ===
  kbSources: () => fetchJson<{ items: KbSource[]; max_upload_mb: number; supported: string[] }>("/api/kb/sources"),
  uploadKbSource: (payload: { filename: string; content: string; name?: string }) =>
    fetchJson<KbSource>("/api/kb/sources/upload", { method: "POST", body: JSON.stringify(payload) }),
  addKbServerSource: (payload: { path: string; name?: string; format?: string }) =>
    fetchJson<KbSource>("/api/kb/sources/server", { method: "POST", body: JSON.stringify(payload) }),
  addKbRemoteSource: (payload: { url: string; name?: string; format?: string; headers?: Record<string, string> }) =>
    fetchJson<KbSource>("/api/kb/sources/remote", { method: "POST", body: JSON.stringify(payload) }),
  addKbDefaultSource: () => fetchJson<KbSource>("/api/kb/sources/default", { method: "POST" }),
  updateKbSource: (id: string, data: Record<string, unknown>) =>
    fetchJson<KbSource>(`/api/kb/sources/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteKbSource: (id: string) =>
    fetchJson<{ deleted: string }>(`/api/kb/sources/${id}`, { method: "DELETE" }),
  syncKbSource: (id: string) =>
    fetchJson<{ id: string; synced_at: string; rows: number }>(`/api/kb/sources/${id}/sync`, { method: "POST" }),
  previewKbSource: (id: string) => fetchJson<KbPreview>(`/api/kb/sources/${id}/preview?limit=5`),
  /** 立即把全部来源并入选题池（等价于每天 6:00 的 kb_scan，手动触发一次） */
  importKbToPool: () =>
    fetchJson<{ total: number; inserted: number }>("/api/kb/import", { method: "POST" }),
  // === 行业模板 / 热点源 ===
  presets: () => fetchJson<{ items: IndustryPreset[] }>("/api/presets"),
  savePreset: (key: string, data: Omit<IndustryPreset, "key" | "is_builtin" | "version">) =>
    fetchJson<IndustryPreset>(`/api/presets/${encodeURIComponent(key)}`, { method: "PUT", body: JSON.stringify(data) }),
  deletePreset: (key: string) =>
    fetchJson<{ key: string; deleted: boolean }>(`/api/presets/${encodeURIComponent(key)}`, { method: "DELETE" }),
  restorePreset: (key: string) =>
    fetchJson<IndustryPreset>(`/api/presets/${encodeURIComponent(key)}/restore`, { method: "POST" }),
  presetVersions: (key: string) =>
    fetchJson<{ items: { version: number; snapshot: IndustryPreset; created_at: string }[] }>(`/api/presets/${encodeURIComponent(key)}/versions`),
  applyPreset: (key: string) =>
    fetchJson<{ key: string; message: string; dimensions: string[]; platform_notes: Record<string, string> }>(
      `/api/presets/${key}/apply`, { method: "POST" },
    ),
  hotSources: () => fetchJson<{ items: HotSource[] }>("/api/hot_topics/sources"),
  saveHotSources: (items: HotSource[]) =>
    fetchJson<{ items: HotSource[] }>("/api/hot_topics/sources", { method: "PUT", body: JSON.stringify({ items }) }),
  testHotSource: (index: number) =>
    fetchJson<{ ok: boolean; status: "success" | "failed" | "empty"; source: string; item_count: number; sample_titles: string[]; message: string }>(
      `/api/hot_topics/sources/${index}/test`, { method: "POST" },
    ),
  saveWebhook: (channel: "wecom" | "feishu", url: string) =>
    fetchJson<{ ok: boolean; channel: string; enabled: boolean; has_webhook_url: boolean }>(
      "/api/notify/webhook", { method: "POST", body: JSON.stringify({ channel, url }) },
    ),
  // === 主动运营目标（账号组 SQLite 业务数据）===
  opsTargets: () => fetchJson<{ items: OpsTargetsView }>("/api/ops-targets"),
  saveOpsTargets: (items: OpsTargetsView) =>
    fetchJson<{ items: OpsTargetsView; message: string }>("/api/ops-targets", {
      method: "PUT", body: JSON.stringify(items),
    }),
  // === G-13 / G-24 运行时配置 ===
  config: () => fetchJson<SystemConfigView>("/api/config"),
  configAuditPage: (page: number, pageSize = 20) =>
    fetchJson<ConfigAuditPage>(`/api/config/audit?page=${page}&page_size=${pageSize}`),
  configMetadata: () => fetchJson<{ items: ConfigMetadataItem[] }>("/api/config/metadata"),
  patchConfig: (updates: Record<string, unknown>) =>
    fetchJson<{ changed: { key: string; old: unknown; new: unknown }[]; noop: boolean; restart_required?: boolean; config: SystemConfigView["editable"] }>(
      "/api/config", { method: "PATCH", body: JSON.stringify({ updates }) },
    ),
  /** R23：Key 只写 config/.env（chmod 600）；返回值只有状态，无明文 */
  saveLlmKey: (apiKey: string, target: "primary" | "fallback" = "primary") =>
    fetchJson<{ ok: boolean; target: string; has_key: boolean }>("/api/config/llm_key", {
      method: "POST", body: JSON.stringify({ api_key: apiKey, target }),
    }),
  notifyRules: () => fetchJson<NotifyRule[]>("/api/notify_rules"),
  createNotifyRule: (data: Partial<NotifyRule>) =>
    fetchJson<{ id: number; message: string }>("/api/notify_rules", {
      method: "POST", body: JSON.stringify(data),
    }),
  updateNotifyRule: (id: number, data: Partial<NotifyRule>) =>
    fetchJson<{ id: number; message: string }>(`/api/notify_rules/${id}`, {
      method: "PATCH", body: JSON.stringify(data),
    }),
  deleteNotifyRule: (id: number) =>
    fetchJson<{ message: string }>(`/api/notify_rules/${id}`, { method: "DELETE" }),
  /** platform_styles 表：平台文案风格与话题标签（改后下次生成即生效） */
  platformStyles: () => fetchJson<{ items: PlatformStyle[] }>("/api/platform_styles"),
  updatePlatformStyle: (
    platform: Platform,
    data: Partial<Pick<PlatformStyle, "title_style" | "body_style" | "tone" | "hashtags">>,
  ) =>
    fetchJson<{ platform: Platform; changed: number; fields?: Record<string, string>; message?: string }>(
      `/api/platform_styles/${platform}`, { method: "PATCH", body: JSON.stringify(data) },
    ),
  // === 运营控制台 ===
  /** days = 展示窗口（1=今日，7/30/90）；与 R12 评分 90 天口径独立 */
  opsKpi: (days = 1) => fetchJson<OpsKpiResponse>(`/api/ops_kpi?days=${days}`),
  /** 全局运营软暂停（人工开关，与熔断无关） */
  opsPause: () =>
    fetchJson<{ paused: boolean; updated_at: string | null; updated_by: string | null }>("/api/ops/pause"),
  setOpsPause: (paused: boolean) =>
    fetchJson<{ paused: boolean }>("/api/ops/pause", {
      method: "POST",
      body: JSON.stringify({ paused }),
    }),
  /** 通知通道自检（不落 notifications 表，不泄露 webhook） */
  notifyTest: () =>
    fetchJson<{ results: Record<string, "ok" | "failed" | "disabled"> }>("/api/notify/test", {
      method: "POST",
    }),
  opsLogs: (platform?: Platform, action_type?: string, status?: string, limit = 100) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (platform) p.set("platform", platform);
    if (action_type) p.set("action_type", action_type);
    if (status) p.set("status", status);
    return fetchJson<{ items: OpsLogItem[]; total: number }>(`/api/ops_logs?${p}`);
  },
  resolveOpsLog: (id: number, action: "sent" | "not_sent") =>
    fetchJson<{ id: number; status: string; resolved_at?: string; already_resolved: boolean }>(
      `/api/ops_logs/${id}/resolve`, { method: "POST", body: JSON.stringify({ action }) },
    ),
  opsLogsPage: (page: number, pageSize = 20, filters: { platform?: Platform; actionType?: string; status?: string; dateFrom?: string; dateTo?: string; recordId?: number } = {}) => {
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (filters.recordId) p.set("record_id", String(filters.recordId));
    if (filters.platform) p.set("platform", filters.platform);
    if (filters.actionType) p.set("action_type", filters.actionType);
    if (filters.status) p.set("status", filters.status);
    if (filters.dateFrom) p.set("date_from", filters.dateFrom);
    if (filters.dateTo) p.set("date_to", filters.dateTo);
    return fetchJson<PageResult<OpsLogItem>>(`/api/ops_logs?${p.toString()}`);
  },
  taskRuns: (taskType?: string, status?: string, limit = 100) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (taskType) p.set("task_type", taskType);
    if (status) p.set("status", status);
    return fetchJson<TaskRunItem[]>(`/api/task_runs?${p}`);
  },
  taskRunsPage: (page: number, pageSize = 20, filters: { taskType?: string; status?: string; platform?: Platform; dateFrom?: string; dateTo?: string; recordId?: number; roundId?: number } = {}) => {
    const p = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (filters.recordId) p.set("record_id", String(filters.recordId));
    if (filters.roundId) p.set("round_id", String(filters.roundId));
    if (filters.taskType) p.set("task_type", filters.taskType);
    if (filters.status) p.set("status", filters.status);
    if (filters.platform) p.set("platform", filters.platform);
    if (filters.dateFrom) p.set("date_from", filters.dateFrom);
    if (filters.dateTo) p.set("date_to", filters.dateTo);
    return fetchJson<PageResult<TaskRunItem>>(`/api/task_runs?${p.toString()}`);
  },
  notifications: (level?: string, limit = 50) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (level) p.set("level", level);
    return fetchJson<NotificationItem[]>(`/api/notifications?${p}`);
  },
  notificationsPage: (page: number, pageSize = 20, readStatus: "all" | "unread" | "read" = "all") =>
    fetchJson<PageResult<NotificationItem> & { unread_total: number }>(
      `/api/notifications?page=${page}&page_size=${pageSize}&read_status=${readStatus}`,
    ),
  markNotificationRead: (id: number) =>
    fetchJson<{ id: number; read_at: string }>(`/api/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () =>
    fetchJson<{ updated: number }>("/api/notifications/read-all", { method: "POST" }),
  schedulerJobs: () => fetchJson<{ jobs: SchedulerJob[]; count: number; running: boolean }>("/api/scheduler/jobs"),
  /** R17：pending_verify 人工核实收口（sent=已发出 / not_sent=未发出） */
  resolveTaskRun: (id: number, action: "sent" | "not_sent") =>
    fetchJson<{ id: number; status: string }>(`/api/task_runs/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }),
  /** 立即触发一次定时任务（§8.3 手动触发验证） */
  runSchedulerJob: (jobId: string) =>
    fetchJson<{ job_id: string; triggered_at: string }>(`/api/scheduler/jobs/${jobId}/run`, {
      method: "POST",
    }),
  rhythm: () => fetchJson<RhythmConfig>("/api/rhythm"),
  // === G-09 母计划 / 内容编辑 / 生产线 ===
  plans: () => fetchJson<PlanResponse>("/api/plans"),
  savePlan: (data: MasterPlan) =>
    fetchJson<SavePlanResult>("/api/plans", { method: "POST", body: JSON.stringify(data) }),
  suggestPlanFromGoal: (goal_id: string) =>
    fetchJson<PlanSuggestion>("/api/plans/suggest_from_goal", { method: "POST", body: JSON.stringify({ goal_id }) }),
  // —— 选题池 ——
  topicPool: (q: { q?: string; dimension?: string; source?: string; status?: string; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => { if (v !== undefined && v !== "") p.set(k, String(v)); });
    return fetchJson<TopicPoolResponse>(`/api/topics?${p}`);
  },
  createTopic: (data: { title: string; dimension: string; summary?: string; link?: string }) =>
    fetchJson<{ id: string; created: boolean; restored: boolean }>("/api/topics", { method: "POST", body: JSON.stringify(data) }),
  deleteTopics: (ids: string[], reason = "") =>
    fetchJson<{ excluded: string[]; not_found: string[] }>("/api/topics/delete", { method: "POST", body: JSON.stringify({ ids, reason }) }),
  restoreTopics: (ids: string[]) =>
    fetchJson<{ restored: string[] }>("/api/topics/restore", { method: "POST", body: JSON.stringify({ ids }) }),
  purgeTopics: (ids: string[]) =>
    fetchJson<{ deleted: string[]; kept: string[] }>("/api/topics/purge", { method: "POST", body: JSON.stringify({ ids }) }),
  // —— 删除 ——
  deleteContents: (ids: string[]) =>
    fetchJson<DeleteContentsResult>("/api/contents/delete", { method: "POST", body: JSON.stringify({ ids }) }),
  deleteRound: (id: number) =>
    fetchJson<{ round_number: number; contents_deleted: number; platform_drafts_left: number }>(`/api/rounds/${id}`, { method: "DELETE" }),
  // —— Agent 编排 / 诊断 ——
  orchestration: () => fetchJson<OrchestrationView>("/api/orchestration"),
  saveOrchestration: (steps: { id: string; enabled: boolean }[]) =>
    fetchJson<OrchestrationView>("/api/orchestration", { method: "PUT", body: JSON.stringify({ steps }) }),
  validateOrchestration: (steps: { id: string; enabled: boolean }[]) =>
    fetchJson<{ valid: boolean; errors: string[] }>("/api/orchestration/validate", { method: "POST", body: JSON.stringify({ steps }) }),
  resetOrchestration: () => fetchJson<OrchestrationView>("/api/orchestration/reset", { method: "POST" }),
  orchestrationRuns: (limit = 10) => fetchJson<{ runs: OrchestrationRun[] }>(`/api/orchestration/runs?limit=${limit}`),
  diagnose: (live_llm = false) =>
    fetchJson<DiagnoseResult>("/api/diagnostics", { method: "POST", body: JSON.stringify({ live_llm }) }),
  // —— 自动闭环 ——
  autogrow: () => fetchJson<AutogrowStatus>("/api/autogrow"),
  autogrowRun: () => fetchJson<{ action: string; reason: string; round_id?: number }>("/api/autogrow/run", { method: "POST" }),
  contents: (roundId: number, platform?: Platform) => {
    const p = new URLSearchParams({ round_id: String(roundId) });
    if (platform) p.set("platform", platform);
    return fetchJson<{ round_id: number; items: ContentItem[] }>(`/api/contents?${p}`);
  },
  /** G-36：卡片图地址；默认 inline 可直接预览，download=true 才触发下载 */
  contentImageUrl: (id: string, index: number, download = false) =>
    `${API}/api/contents/${id}/images/${index}${download ? "?download=1" : ""}`,
  updateContent: (id: string, data: { title?: string; body?: string; tags?: string[]; reason?: string }) =>
    fetchJson<{ id: string; changed: boolean; title: string; body: string; tags: string[] }>(
      `/api/contents/${id}`, { method: "PATCH", body: JSON.stringify(data) },
    ),
  recheckContent: (id: string) =>
    fetchJson<RecheckResult>(`/api/contents/${id}/recheck`, { method: "POST" }),
  /** 按当前标题/正文重新出图（封面 + 正文分页） */
  regenerateContentCards: (id: string) =>
    fetchJson<{ id: string; image_count: number; image_paths: string[] }>(
      `/api/contents/${id}/cards`, { method: "POST" },
    ),
  rejectContent: (id: string, reason: string) =>
    fetchJson<{
      id: string; affected_drafts: number; review_rejected: boolean;
      platform_drafts_left: number; message: string;
    }>(
      `/api/contents/${id}/reject`, { method: "POST", body: JSON.stringify({ reason }) },
    ),
  resubmitContent: (id: string, reason: string) =>
    fetchJson<{ id: string; review_rejected: boolean; message: string }>(
      `/api/contents/${id}/resubmit`,
      { method: "POST", body: JSON.stringify({ reason }) },
    ),
  pipeline: (roundId: number) =>
    fetchJson<PipelineResponse>(`/api/rounds/${roundId}/pipeline`),
  // === P1 — AI 证据审计链 ===
  aiCalls: (limit = 200, step?: string) => {
    const p = new URLSearchParams({ limit: String(limit) });
    if (step) p.set("step", step);
    return fetchJson<AiCallRecord[]>(`/api/ai_calls?${p}`);
  },
  userOverride: (limit = 200) =>
    fetchJson<UserOverrideRecord[]>(`/api/user_override?limit=${limit}`),
  redlineHistory: (limit = 200) =>
    fetchJson<RedlineRecord[]>(`/api/redline_history?limit=${limit}`),
  // === 账号管理 ===
  accounts: () => fetchJson<{ accounts: AccountInfo[] }>("/api/accounts"),
  platformAccounts: () => fetchJson<{ items: RegisteredPlatformAccount[]; total: number; active_total: number }>("/api/platform-accounts"),
  savePlatformAccount: (id: number | null, data: { platform: Platform; account_handle: string; display_name: string; status: "active" | "inactive"; notes: string }) =>
    fetchJson<RegisteredPlatformAccount>(id == null ? "/api/platform-accounts" : `/api/platform-accounts/${id}`, {
      method: id == null ? "POST" : "PUT", body: JSON.stringify(data),
    }),
  deletePlatformAccount: (id: number) =>
    fetchJson<void>(`/api/platform-accounts/${id}`, { method: "DELETE" }),
  /** 启动扫码登录；restart=true 会先结束仍在运行的旧登录进程 */
  startLogin: (platform: Platform, restart = false) =>
    fetchJson<LoginStatus>(`/api/accounts/${platform}/login${restart ? "?restart=true" : ""}`, { method: "POST" }),
  loginStatus: (platform: Platform) => fetchJson<LoginStatus>(`/api/accounts/${platform}/login_status`),
  /** 只读校验平台登录态并读取昵称、头像。 */
  accountIdentity: (platform: Platform) => fetchJson<AccountIdentity>(`/api/accounts/${platform}/identity`),
  logout: (platform: Platform) =>
    fetchJson<{ status: string; platform: string }>(`/api/accounts/${platform}/logout`, { method: "POST" }),
  /** 短信验证码回填（登录进入 sms_required 时） */
  submitLoginSms: (platform: Platform, code: string) =>
    fetchJson<{ ok: boolean }>(`/api/accounts/${platform}/sms`, { method: "POST", body: JSON.stringify({ code }) }),
  cancelLogin: (platform: Platform) =>
    fetchJson<{ status: string; platform: string }>(`/api/accounts/${platform}/cancel_login`, { method: "POST" }),
  // === 激活与留存闭环 ===
  startActivation: () => fetchJson<{ first_seen_at: string }>("/api/activation/start", { method: "POST" }),
  activationStatus: () => fetchJson<ActivationStatus>("/api/activation/status"),
  createActivationSamples: (data: { account_description: string; platform: Platform; idempotency_key: string }) =>
    fetchJson<{ sample_run_id: string; status: string; poll_after_ms?: number; deduplicated?: boolean;
      error_code?: string; error_message?: string }>("/api/activation/samples", {
        method: "POST", body: JSON.stringify(data),
      }),
  activationSamples: (id: string) => fetchJson<ActivationSampleRun>(`/api/activation/samples/${id}`),
  activationSavedSamples: () => fetchJson<{ items: ActivationSavedSample[] }>("/api/activation/saved"),
  promoteActivationSample: (sampleContentId: string, roundId: number) =>
    fetchJson<{ sample_content_id: string; content_id: string; draft_id?: string; round_id: number; already_promoted: boolean }>(
      `/api/activation/saved/${sampleContentId}/promote`,
      { method: "POST", body: JSON.stringify({ round_id: roundId }) },
    ),
  deleteActivationSamples: (id: string) =>
    fetchJson<{ sample_run_id: string; deleted: boolean }>(`/api/activation/samples/${id}`, { method: "DELETE" }),
  activationSampleEvent: (runId: string, contentId: string, event_type: "copied" | "saved" | "rejected", reason?: string) =>
    fetchJson<{ sample_content_id: string; event: string; recorded: boolean }>(
      `/api/activation/samples/${runId}/contents/${contentId}/events`,
      { method: "POST", body: JSON.stringify({ event_type, reason }) },
    ),
  updateDraftNoteUrl: (draftId: string, note_url: string) =>
    fetchJson<{ draft_id: string; note_url: string | null; updated: boolean }>(
      `/api/drafts/${draftId}/note_url`, { method: "POST", body: JSON.stringify({ note_url }) },
    ),
  metricSnapshots: (draftId: string) =>
    fetchJson<{ items: {
      id: string; draft_id: string; content_id: string; platform: Platform; source: string;
      views: number | null; likes: number | null; collects: number | null; comments: number | null;
      shares: number | null; measured_at: string; recorded_at: string;
      correction_of: string | null;
    }[]; total: number }>(`/api/metrics/snapshots?draft_id=${encodeURIComponent(draftId)}`),
  submitManualMetrics: (draftId: string, data: {
    views?: number | null; likes?: number | null; collects?: number | null;
    comments?: number | null; shares?: number | null;
    measured_at?: string; correction_of?: string | null;
  }) => fetchJson<{ snapshot_id: string; source: string; recorded_at: string }>(
    `/api/drafts/${draftId}/metrics/manual`, { method: "POST", body: JSON.stringify(data) },
  ),
  weeklyReport: (week_start?: string, platform?: Platform) => {
    const p = new URLSearchParams();
    if (week_start) p.set("week_start", week_start);
    if (platform) p.set("platform", platform);
    return fetchJson<WeeklyReport>(`/api/reports/weekly?${p}`);
  },
  weeklyActionFeedback: (data: { suggestion_key: string; decision: "adopted" | "ignored";
    outcome?: "success" | "no_effect" | "unclear"; outcome_note?: string }) =>
    fetchJson<{ suggestion_key: string; decision: string; outcome: string; outcome_note?: string | null }>(
      "/api/reports/weekly/actions", { method: "POST", body: JSON.stringify(data) },
    ),
};
