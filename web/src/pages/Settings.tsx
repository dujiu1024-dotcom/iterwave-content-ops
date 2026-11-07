import { useEffect, useState } from "react";
import { navigate, useUrlTab } from "../nav";
import { useApp } from "../AppContext";
import EmptyHint from "../components/EmptyHint";
import TenantManagerPane from "../components/TenantManagerPane";
import RuntimePolicyEditor from "../components/RuntimePolicyEditor";
import { AdvancedEditor, ImageModelEditor, KbEditor, LlmEditor, PlatformStyleEditor, PlatformToggles, TimeoutEditor } from "../components/ConfigEditors";
import { api, type SystemStatus, type SystemConfigView, type HealthReport } from "../api";
import { displayPlatformLabel } from "../displayLabels";

const PLATFORM_LABELS: Record<string, string> = {
  xhs: "小红书",
  douyin: "抖音",
  kuaishou: "快手",
  wechat_channels: "视频号",
};

const AUDIT_REMEDIATION_GUIDANCE: Record<string, string> = {
  business_profile_current_version_missing: "核对 business_profile_versions 历史版本与当前 version_no；从可信历史版本恢复缺失版本，保留原始资料和变更记录。",
  business_profile_history_json_invalid: "导出违规行，优先从数据库备份或原始资料重建版本快照；不得直接覆盖历史版本。",
  business_catalog_json_invalid: "导出违规行，依据原始资料和字段证据重建 JSON，再通过正常资料编辑流程写入新版本。",
  effective_master_plan_snapshot_missing: "核对生效计划版本及审核记录；从对应版本证据恢复快照，无法确认时重新审核，不得伪造快照。",
  master_plan_snapshot_json_invalid: "导出违规行，从对应计划版本和业务资料版本重建快照；保留原始损坏记录供审计。",
  master_plan_snapshot_profile_version_missing: "核对快照引用的资料版本；恢复缺失版本或将计划重新基于当前有效资料生成并审核。",
  original_asset_current_version_missing: "核对素材版本历史与文件哈希；确认真实版本后恢复 current_version_no，不确定时先隔离素材。",
  original_asset_metadata_json_invalid: "从素材原文件、版本记录及导入来源重建元数据；保留损坏记录和恢复依据。",
  original_asset_version_metadata_json_invalid: "从对应素材版本的原文件及处理记录重建元数据，不得猜测版本内容。",
  original_asset_analysis_json_invalid: "重新运行素材分析生成新分析记录；旧记录保留，不直接覆盖审计历史。",
  production_task_asset_usage_version_mismatch: "核对生产任务使用时的素材版本快照与原始版本；确认后创建更正记录，不静默改写任务历史。",
  plan_change_audit_json_invalid: "从相关计划版本、任务快照和备份重建审计证据；原始审计行只读保留。",
  north_star_snapshot_json_invalid: "从对应北极星版本及其来源证据重建快照；无法恢复时重新确认北极星版本。",
  master_plan_north_star_snapshot_invalid: "核对母计划引用的北极星快照与版本归属；修复引用前需重新验证计划审核依据。",
  master_plan_resource_baseline_json_invalid: "从资源基线来源数据重建 JSON，并核对租户、版本及生效时间。",
  master_plan_north_star_reference_missing: "核对北极星版本历史；恢复真实引用或重新生成并审核母计划，不得指向任意现存版本。",
  master_plan_north_star_attribution_invalid: "核对归因状态与对应北极星证据，按计划版本重新执行归因审核。",
  metric_snapshot_orphan_draft: "导出全部快照行，核对业务记录关联与账号组归属；确认后通过受控迁移修复关联，禁止直接删除快照。",
  metric_snapshot_orphan_content: "导出全部快照行，核对原内容及草稿归属；仅在找到可信内容来源后恢复关联。",
  metric_snapshot_correction_chain_broken: "核对 correction_of 指向的原始快照、租户和草稿；通过新更正记录恢复链路，不改写历史指标。",
  metric_snapshot_duplicate_idempotency_key: "比较 payload_hash、记录时间和来源，确认重复提交关系后制定保留策略；不自动删除任何指标记录。",
  manual_metric_draft_state_mismatch: "核对 user_confirmed_at、发布证据和人工快照；确认真实发布后通过草稿状态机补齐状态，不能仅按指标记录强制改状态。",
  metric_fact_orphan_content: "核对事实来源快照与内容归属；确认来源后修复投影/关联，保留原始事实。",
  metric_fact_orphan_draft: "核对事实来源快照与草稿归属；确认来源后修复投影/关联，不直接删除事实。",
  metric_fact_correction_chain_broken: "核对事实与源快照的 correction_of 链；通过受控重建或新更正记录修复，保留原链证据。",
  spiral_round_decision_json_invalid: "从 round transition log、复盘报告和审计记录恢复决策证据；无法恢复时将该轮标记为需人工复核。",
};

const SCORING_MODE_LABELS: Record<string, string> = {
  cold_start: "冷启动",
  sample_accumulating: "样本积累",
  stable: "稳定运行",
};

const SETTINGS_TABS = [
  { k: "runtime", l: "运行与配额" },
  { k: "style", l: "平台文案风格" },
  { k: "model", l: "知识库与模型" },
  { k: "platform", l: "平台与账号" },
  { k: "rhythm", l: "节奏与评分权重" },
  { k: "policy", l: "运行策略参数" },
  { k: "system", l: "运行状态" },
  { k: "tenants", l: "账号组" },
] as const;
type SettingsTab = typeof SETTINGS_TABS[number]["k"];

export default function Settings() {
  const [tab, setTab] = useUrlTab<SettingsTab>(SETTINGS_TABS.map((t) => t.k), "runtime");
  const { showToast, withPending, isPending } = useApp();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cfgView, setCfgView] = useState<SystemConfigView | null>(null);
  const [auditItems, setAuditItems] = useState<SystemConfigView["audit"]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditPage, setAuditPage] = useState(1);
  const [auditPages, setAuditPages] = useState(1);
  // 运行时配置表单（只保留白名单字段，保存时按改动差量提交）
  const [quotas, setQuotas] = useState<Record<string, Record<string, string>>>({});
  const [opsWindow, setOpsWindow] = useState<{ start: string; end: string }>({ start: "", end: "" });
  const [cooldown, setCooldown] = useState("");
  const [businessTimezone, setBusinessTimezone] = useState("Asia/Shanghai");
  const [hotFilters, setHotFilters] = useState({
    heat: "", heatOn: true, hours: "", timeOn: true,
  });

  const exportHealthAudit = async () => {
    try {
      const report = await api.healthAuditExport();
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `iterwave-governance-audit-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      showToast("完整审计明细已导出", "success");
    } catch (e) {
      showToast("审计明细导出失败，请稍后重试。", "error");
    }
  };

  const load = async () => {
    setErr(null);
    try {
      setStatus(await api.status());
    } catch (e) {
      setErr("设置读取失败，请稍后重试。");
    }
    try {
      setHealth(await api.health());
      setHealthErr(null);
    } catch (e) {
      setHealthErr("健康状态读取失败，请稍后重试。");
    }
    await loadConfig();
  };

  /** G-13：运行时配置来自 /api/config（白名单字段 + 审计） */
  const loadConfig = async () => {
    try {
      const [v, audit] = await Promise.all([
        api.config(),
        api.configAuditPage(auditPage, 20),
      ]);
      setCfgView(v);
      setAuditItems(audit.items);
      setAuditTotal(audit.total);
      setAuditPages(audit.pages);
      const q: Record<string, Record<string, string>> = {};
      for (const [p, row] of Object.entries(v.editable.daily_quotas)) {
        q[p] = {
          passive_replies: _str(row.passive_replies),
          active_comments: _str(row.active_comments),
          follows: _str(row.follows),
        };
      }
      setQuotas(q);
      setOpsWindow({
        start: _str(v.editable.ops_window.start_hour),
        end: _str(v.editable.ops_window.end_hour),
      });
      setCooldown(_str(v.editable.cooldown_minutes));
      setBusinessTimezone(v.editable.business_timezone || "Asia/Shanghai");
      setHotFilters({
        heat: _str(v.editable.hot_topics.heat_threshold),
        heatOn: v.editable.hot_topics.heat_filter_enabled ?? true,
        hours: _str(v.editable.hot_topics.time_window_hours),
        timeOn: v.editable.hot_topics.time_filter_enabled ?? true,
      });
    } catch (e) {
      setErr("设置读取失败，请稍后重试。");
    }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => {
    api.configAuditPage(auditPage, 20).then((result) => {
      setAuditItems(result.items);
      setAuditTotal(result.total);
      setAuditPages(result.pages);
    }).catch(() => setErr("审计记录读取失败，请稍后重试。"));
  }, [auditPage]);

  /** 只提交真正改动的字段（后端按白名单校验，越权 400） */
  const saveConfig = async () => {
    if (!cfgView) return;
    const updates: Record<string, unknown> = {};
    const ed = cfgView.editable;
    for (const [p, row] of Object.entries(quotas)) {
      for (const f of ["passive_replies", "active_comments", "follows"] as const) {
        const val = Number(row[f]);
        if (row[f] !== "" && val !== ed.daily_quotas[p]?.[f]) {
          updates[`rhythm.daily_quotas.${p}.${f}`] = val;
        }
      }
    }
    if (opsWindow.start !== "" && Number(opsWindow.start) !== ed.ops_window.start_hour) {
      updates["rhythm.windows.ops.start_hour"] = Number(opsWindow.start);
    }
    if (opsWindow.end !== "" && Number(opsWindow.end) !== ed.ops_window.end_hour) {
      updates["rhythm.windows.ops.end_hour"] = Number(opsWindow.end);
    }
    if (cooldown !== "" && Number(cooldown) !== ed.cooldown_minutes) {
      updates["circuit_breaker.cooldown_minutes"] = Number(cooldown);
    }
    if (businessTimezone.trim() && businessTimezone.trim() !== ed.business_timezone) {
      updates["business_timezone"] = businessTimezone.trim();
    }
    if (hotFilters.heat !== "" && Number(hotFilters.heat) !== ed.hot_topics.heat_threshold) {
      updates["hot_topics.filters.heat_threshold"] = Number(hotFilters.heat);
    }
    if (hotFilters.heatOn !== (ed.hot_topics.heat_filter_enabled ?? true)) {
      updates["hot_topics.filters.heat_filter_enabled"] = hotFilters.heatOn;
    }
    if (hotFilters.hours !== "" && Number(hotFilters.hours) !== ed.hot_topics.time_window_hours) {
      updates["hot_topics.filters.time_window_hours"] = Number(hotFilters.hours);
    }
    if (hotFilters.timeOn !== (ed.hot_topics.time_filter_enabled ?? true)) {
      updates["hot_topics.filters.time_filter_enabled"] = hotFilters.timeOn;
    }

    if (Object.keys(updates).length === 0) {
      showToast("没有需要保存的改动", "info");
      return;
    }
    try {
      const res = await withPending("config:save", () => api.patchConfig(updates));
      if (!res) return;
      showToast(res.restart_required
        ? `已保存 ${res.changed.length} 项配置；业务时区将在服务重启后完整生效`
        : `✓ 已保存 ${res.changed.length} 项配置`, res.restart_required ? "info" : "success");
      await loadConfig();
    } catch (e: any) {
      showToast("配置保存失败，请检查填写内容后重试。", "error");
    }
  };

  const enabledNames = status?.enabled_platforms
    .map((p) => PLATFORM_LABELS[p] ?? displayPlatformLabel(p))
    .join(" · ") ?? "加载中…";

  const brokenNames = status?.circuit_broken_platforms
    .map((p) => PLATFORM_LABELS[p] ?? p)
    .join(" · ") ?? "";

  const modeLabel = status
    ? SCORING_MODE_LABELS[status.scoring_mode] ?? status.scoring_mode
    : "加载中…";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">系统设置</div>
          <h1 className="page-title">系统设置</h1>
          <div className="page-desc">所有设置保存后立即生效，并记录变更历史；按分类在下方页签中修改。</div>
        </div>
        <div className="head-actions">
          <button className="btn sm" onClick={load}>↻ 刷新</button>
        </div>
      </div>

      <div className="tabs" style={{ marginBottom: 16, alignSelf: "flex-start", display: "inline-flex" }}>
        {SETTINGS_TABS.map((t) => (
          <button key={t.k} type="button" className={`tab ${tab === t.k ? "active" : ""}`} onClick={() => setTab(t.k)}>{t.l}</button>
        ))}
      </div>

      {/* G-13：运行时配置（白名单可写）*/}
      {tab === "runtime" && (
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-head">
          <div>
            <div className="panel-title"><span className="icon">⚙</span>运行与配额</div>
            <div className="panel-sub">
              每日配额、运营时段、熔断冷却与热点门限；保存后立即生效
            </div>
          </div>
          <div className="head-actions">
            <button className="btn primary sm" disabled={!cfgView || isPending("config:save")} onClick={saveConfig}>
              {isPending("config:save") ? "保存中…" : "保存配置"}
            </button>
          </div>
        </div>

        {!cfgView ? (
          <div className="note">加载配置中…</div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>平台</th><th>被动回复/日</th><th>主动评论/日</th><th>关注/日</th></tr>
                </thead>
                <tbody>
                  {Object.entries(quotas).length === 0 && (
                    <tr><td colSpan={4}>
                      <EmptyHint className="" next="在配置文件中设置各平台每日配额后刷新">暂无平台配额配置</EmptyHint>
                    </td></tr>
                  )}
                  {Object.entries(quotas).map(([p, row]) => (
                    <tr key={p}>
                      <td>{PLATFORM_LABELS[p] ?? p}</td>
                      {(["passive_replies", "active_comments", "follows"] as const).map((f) => (
                        <td key={f}>
                          <input
                            className="field"
                            type="number"
                            min={0}
                            value={row[f]}
                            style={{ width: 90 }}
                            onChange={(e) => setQuotas((prev) => ({ ...prev, [p]: { ...prev[p], [f]: e.target.value } }))}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="metric" style={{ marginTop: 14, maxWidth: 420 }}>
              <div className="metric-label">业务时区（IANA）</div>
              <input className="field" value={businessTimezone}
                placeholder="Asia/Shanghai"
                onChange={(e) => setBusinessTimezone(e.target.value)} />
              <div className="metric-foot">例如 Asia/Shanghai、Asia/Tokyo、UTC。保存后影响运营时段和每日配额日界线；定时任务将在服务重启后切换。</div>
            </div>
            <div className="grid three" style={{ marginTop: 14 }}>
              <div className="metric">
                <div className="metric-label">运营时段（业务时区）</div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
                  <input className="field" type="number" min={0} max={23} value={opsWindow.start}
                    style={{ width: 70 }}
                    onChange={(e) => setOpsWindow((w) => ({ ...w, start: e.target.value }))} />
                  <span style={{ color: "var(--muted)" }}>→</span>
                  <input className="field" type="number" min={0} max={23} value={opsWindow.end}
                    style={{ width: 70 }}
                    onChange={(e) => setOpsWindow((w) => ({ ...w, end: e.target.value }))} />
                  <span className="metric-foot">时</span>
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">熔断冷却（分钟）</div>
                <input className="field" type="number" min={1} max={1440} value={cooldown}
                  style={{ width: 100, marginTop: 6 }}
                  onChange={(e) => setCooldown(e.target.value)} />
              </div>
              <div className="metric">
                <div className="metric-label">热点：热度门限 / 时效窗口</div>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
                  <input className="field" type="number" min={0} value={hotFilters.heat}
                    style={{ width: 90 }}
                    onChange={(e) => setHotFilters((f) => ({ ...f, heat: e.target.value }))} />
                  <input className="field" type="number" min={1} max={720} value={hotFilters.hours}
                    style={{ width: 70 }}
                    onChange={(e) => setHotFilters((f) => ({ ...f, hours: e.target.value }))} />
                  <span className="metric-foot">小时</span>
                </div>
                <div style={{ display: "flex", gap: 14, marginTop: 8, fontSize: 12 }}>
                  <label><input type="checkbox" checked={hotFilters.heatOn}
                    onChange={(e) => setHotFilters((f) => ({ ...f, heatOn: e.target.checked }))} /> 启用热度门</label>
                  <label><input type="checkbox" checked={hotFilters.timeOn}
                    onChange={(e) => setHotFilters((f) => ({ ...f, timeOn: e.target.checked }))} /> 启用时效门</label>
                </div>
              </div>
            </div>

            <div className="note" style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap" }}>
              <span className="tag red">维度匹配：强制开启</span>
              <span className="tag red">可发布性检查：强制开启</span>
              <span style={{ color: "var(--muted)" }}>
                这两道门没有关闭入口；直接调 API 关闭会被后端拒绝（400）。
              </span>
            </div>

            {auditItems.length > 0 && (
              <div className="table-wrap" style={{ marginTop: 14 }}>
                <table>
                  <thead><tr><th>#</th><th>配置项</th><th>旧值</th><th>新值</th><th>时间</th></tr></thead>
                  <tbody>
                    {auditItems.map((a, index) => (
                      <tr key={a.id}>
                        <td style={{ color: "var(--subtle)", fontSize: 11 }}>{index + 1}</td>
                        <td style={{ fontSize: 12 }} title={a.key}>{configKeyLabel(a.key)}</td>
                        <td style={{ color: "var(--muted)" }}>{a.old_value ?? <span style={{ color: "var(--subtle)" }}>—</span>}</td>
                        <td>{a.new_value ?? <span style={{ color: "var(--subtle)" }}>—</span>}</td>
                        <td style={{ fontSize: 11, color: "var(--muted)" }}>{a.changed_at ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="flex between center" style={{ padding: "12px 0 0", gap: 10, flexWrap: "wrap" }}>
                  <span className="note">共 {auditTotal} 条配置变更 · 第 {auditPage}/{auditPages} 页 · 每页 20 条</span>
                  <div className="flex" style={{ gap: 6 }}>
                    <button className="btn sm" disabled={auditPage <= 1} onClick={() => setAuditPage((p) => Math.max(1, p - 1))}>上一页</button>
                    <button className="btn sm" disabled={auditPage >= auditPages} onClick={() => setAuditPage((p) => Math.min(auditPages, p + 1))}>下一页</button>
                  </div>
                </div>
              </div>
            )}
            {auditTotal === 0 && (
              <EmptyHint next="修改上方任一配额 / 运营时段 / 门限后点「保存配置」，这里会记录每次变更">
                暂无配置变更记录
              </EmptyHint>
            )}
          </>
        )}
      </div>

      )}

      {/* 平台文案风格（platform_styles 表，migrations/016）*/}
      {tab === "style" && (
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-head">
          <div>
            <div className="panel-title"><span className="icon">✎</span>平台文案风格</div>
            <div className="panel-sub">
标题风格、正文结构、语气与话题标签；下一次生成文案时立即生效
            </div>
          </div>
        </div>
        <PlatformStyleEditor />
      </div>
      )}

      {/* 本地知识库：可编辑 */}
      {tab === "model" && (
      <div className="grid two">
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">▣</span>连接本地知识库</div>
              <div className="panel-sub">数据源 + 字段映射 + 同步扫描策略（可在线改，无需重启）</div>
            </div>
          </div>
          {!cfgView ? (
            <div className="note">加载中…</div>
          ) : (
            <KbEditor view={cfgView} onSaved={loadConfig} />
          )}
        </div>

        {/* 主模型与备用：可编辑；Key 只写 .env */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">⇄</span>主模型与备用</div>
              <div className="panel-sub">模型可在线切换；服务密钥保存后不再回显</div>
            </div>
          </div>
          {!cfgView ? (
            <div className="note">加载中…</div>
          ) : (
            <LlmEditor view={cfgView} onSaved={loadConfig} />
          )}
        </div>

        {/* 图片创作模型：与对话模型完全独立 */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">▣</span>图片创作模型（独立通道）</div>
              <div className="panel-sub">与上方对话模型分开配置：商汤主模型 + 火山兜底</div>
            </div>
          </div>
          <ImageModelEditor onSaved={loadConfig} />
        </div>

      </div>
      )}

      {tab === "platform" && (
      <div className="grid two">
        {/* 平台账号与连接：启用开关可编辑 */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">◉</span>平台账号与连接</div>
              <div className="panel-sub">启用或停用平台；登录在「账号与登录」页完成</div>
            </div>
          </div>
          {!cfgView ? (
            <div className="note">加载中…</div>
          ) : (
            <PlatformToggles
              view={cfgView}
              onSaved={() => { loadConfig(); load(); }}
              loggedIn={status?.logged_in_platforms}
            />
          )}
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">▣</span>登录状态</div>
              <div className="panel-sub">扫码登录、过期提醒与登录记录统一在「账号与登录」页管理</div>
            </div>
          </div>
          <div className="list">
            {(["xhs", "douyin", "kuaishou", "wechat_channels"] as const).map((p) => {
              const on = status?.logged_in_platforms?.includes(p);
              return (
                <div className="task-row" key={p}>
                  <i className={`task-prio ${on ? "green" : "orange"}`} />
                  <div className="task-main"><div className="task-title">{PLATFORM_LABELS[p]}</div></div>
                  <span className={`tag ${on ? "green" : "gray"}`}>{on ? "已登录" : "未登录"}</span>
                </div>
              );
            })}
          </div>
          <button type="button" className="btn sm" style={{ marginTop: 12 }} onClick={() => navigate("accounts")}>去扫码登录 →</button>
        </div>
      </div>
      )}

      {tab === "platform" && cfgView && (
        <AccessNotifyPanel view={cfgView} onSaved={loadConfig} />
      )}

      {tab === "rhythm" && (
      <div className="grid two">
        {/* 阶段超时（R8：每个参数只管自己的阶段）— 可编辑 */}
        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">⚙</span>阶段超时与节奏参数</div>
              <div className="panel-sub">每个阶段独立计时，超时后进入人工处理</div>
            </div>
          </div>
          {!cfgView ? (
            <div className="note">加载中…</div>
          ) : (
            <TimeoutEditor view={cfgView} onSaved={loadConfig} />
          )}
        </div>

        {/* 高级参数：评分权重 / 半衰期 / 节奏间隔 / 红线词库（安全底线仍硬锁） */}
        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">⌁</span>评分权重、节奏间隔与红线词库</div>
              <div className="panel-sub">
安全底线不可关闭：维度匹配、可发布性检查，以及系统永不自动点发布
              </div>
            </div>
          </div>
          {!cfgView ? (
            <div className="note">加载中…</div>
          ) : (
            <AdvancedEditor view={cfgView} onSaved={loadConfig} />
          )}
        </div>
      </div>
      )}

      {tab === "policy" && <RuntimePolicyEditor />}

      {tab === "system" && (
      <div className="grid two">
        {/* 运行配置 — 动态从 /api/status 读取 */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">⚙</span>当前运行配置</div>
              <div className="panel-sub">实时读取</div>
            </div>
          </div>
          {err ? (
            <div className="note" style={{ color: "var(--red)", display: "flex", gap: 10, alignItems: "center" }}>
              <span>加载失败: {err}</span>
              <button className="btn sm" onClick={load}>↻ 重试</button>
            </div>
          ) : (
          <div className="list">
            <div className="task-row"><i className="task-prio blue" /><div className="task-main"><div className="task-title">后端服务</div><div className="task-desc">{status ? "运行中" : "连接中…"}</div></div></div>
            <div className="task-row"><i className="task-prio green" /><div className="task-main"><div className="task-title">已启用平台</div><div className="task-desc">{enabledNames}</div></div></div>
            <div className="task-row"><i className="task-prio purple" /><div className="task-main"><div className="task-title">评分模式</div><div className="task-desc">{modeLabel}</div></div></div>
            <div className="task-row"><i className="task-prio orange" /><div className="task-main"><div className="task-title">已发布样本</div><div className="task-desc">{status ? status.published_samples : "—"} 条</div></div></div>
            <div className="task-row"><i className="task-prio blue" /><div className="task-main"><div className="task-title">活跃轮次</div><div className="task-desc">{status ? status.active_rounds : "—"} 个进行中</div></div></div>
            {brokenNames && (
              <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">! 熔断平台</div><div className="task-desc" style={{ color: "var(--danger)" }}>{brokenNames}</div></div></div>
            )}
          </div>
          )}
        </div>

        {/* 数据完整性审计 — 按当前账号组实时读取 */}
        <div className="panel" style={{ gridColumn: "1 / -1" }}>
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">⌕</span>数据完整性审计</div>
              <div className="panel-sub">指标快照、修正链、草稿状态与轮次复盘数据；只读审计，不自动改写业务记录</div>
            </div>
            <button className="btn sm" onClick={() => api.health().then(setHealth).catch(() => setHealthErr("健康状态读取失败，请稍后重试。"))}>↻ 刷新</button>
            <button className="btn sm ghost" onClick={exportHealthAudit}>↓ 导出完整违规明细</button>
          </div>
          {healthErr ? (
            <div className="note" style={{ color: "var(--red)" }}>审计读取失败：{healthErr}</div>
          ) : !health ? (
            <div className="note">审计读取中…</div>
          ) : (
            <>
              <div className="list">
                <div className="task-row"><i className={`task-prio ${health.status === "ok" ? "green" : "red"}`} /><div className="task-main"><div className="task-title">当前账号组状态</div><div className="task-desc">{health.status === "ok" ? "健康" : "存在完整性问题"}</div></div></div>
                <div className="task-row"><i className="task-prio blue" /><div className="task-main"><div className="task-title">治理结构缺失</div><div className="task-desc">{health.governance.missing_governance_objects} 项</div></div></div>
                <div className="task-row"><i className={`task-prio ${health.governance.data_violation_count ? "red" : "green"}`} /><div className="task-main"><div className="task-title">业务数据违规</div><div className="task-desc">{health.governance.data_violation_count} 条</div></div></div>
              </div>
              {health.governance.audit_error && <div className="note">完整性审计未能完成，请导出审计明细并稍后重试。</div>}
              {health.governance.data_violation_count === 0 ? (
                <div className="note">当前已配置的业务数据检查未发现违规。</div>
              ) : (
                <div className="list">
                  {Object.entries(health.governance.data_violations).filter(([, count]) => count > 0).map(([name, count]) => (
                    <div className="task-row" key={name}>
                      <i className="task-prio red" />
                      <div className="task-main">
                        <div className="task-title">{name} · {count} 条</div>
                        <div className="task-desc">
                          {(health.governance.data_violation_examples[name] ?? []).map((item) =>
                            "存在待核查记录"
                          ).join("、") || "暂无样例 ID"}
                        </div>
                        <div className="task-desc" style={{ marginTop: 4 }}>
                          修复路径：{AUDIT_REMEDIATION_GUIDANCE[name] ?? "先导出完整违规明细，核对来源记录、租户归属及版本历史后人工制定修复方案。"}
                        </div>
                        <div className="task-desc" style={{ marginTop: 2 }}>
                          自动修复：当前未开放。所有数据修复均需先核对证据并保留原始记录，避免误删或篡改业务事实。
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* 断点恢复 — 动态 */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">↻</span>断点恢复状态</div>
              <div className="panel-sub">服务重启后自动恢复中断的任务</div>
            </div>
          </div>
          {status?.recovery ? (
          <div className="list">
            <div className="task-row"><i className="task-prio green" /><div className="task-main"><div className="task-title">中断任务</div><div className="task-desc">{status.recovery.interrupted_tasks ?? 0} 个已重登</div></div></div>
            <div className="task-row"><i className="task-prio blue" /><div className="task-main"><div className="task-title">补登记采集</div><div className="task-desc">{(status.recovery.requeued_data_fetch ?? []).length} 篇（{(status.recovery.requeued_data_fetch ?? []).join(", ") || "无"}）</div></div></div>
            <div className="task-row"><i className="task-prio orange" /><div className="task-main"><div className="task-title">卡住轮次</div><div className="task-desc">{(status.recovery.stale_rounds ?? []).length > 0 ? `#${(status.recovery.stale_rounds ?? []).join(", #")}` : "无"}</div></div></div>
            <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">待核实</div><div className="task-desc">{status.recovery.pending_verify ?? 0} 条</div></div></div>
          </div>
          ) : err ? (
          <div className="note" style={{ color: "var(--red)", display: "flex", gap: 10, alignItems: "center" }}>
            <span>加载失败: {err}</span>
            <button className="btn sm" onClick={load}>↻ 重试</button>
          </div>
          ) : (
          <div className="note">加载中…</div>
          )}
        </div>

        {/* 红线规则 — 静态说明 */}
        <div className="panel">
          <div className="panel-head">
            <div>
              <div className="panel-title"><span className="icon">!</span>安全红线</div>
              <div className="panel-sub">系统内置、不可关闭</div>
            </div>
          </div>
          <div className="list">
            <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">永不自动发布</div><div className="task-desc">系统只保存草稿，发布必须由你确认</div></div></div>
            <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">未知不等于 0</div><div className="task-desc">没采集到的数据显示「待采集」，不当作 0 计算</div></div></div>
            <div className="task-row"><i className="task-prio red" /><div className="task-main"><div className="task-title">熔断后三步恢复</div><div className="task-desc">冷却到期、登录检测、轻量操作检测全部通过才恢复</div></div></div>
            <div className="task-row"><i className="task-prio orange" /><div className="task-main"><div className="task-title">平台数据分开比较</div><div className="task-desc">各平台只和自己的历史比，不跨平台横比</div></div></div>
            <div className="task-row"><i className="task-prio orange" /><div className="task-main"><div className="task-title">热点四道筛选</div><div className="task-desc">维度匹配与可发布性不可关；热度与时效门限可调</div></div></div>
          </div>
        </div>
      </div>
      )}
      {tab === "tenants" && <TenantManagerPane />}

    </>
  );
}

/** 配置值 → 输入框字符串；NULL 显示空串（不填 0，R3） */
/** 配置键 → 中文名（变更记录用；未收录的保留原键） */
const KEY_PART: Record<string, string> = {
  ultimate_goal: "账号画像", persona_keywords: "定位关键词", forbidden_directions: "禁止方向", redline_keywords: "红线词库",
  positioning: "定位", goal_mode: "目标模式", platform_personas: "平台画像", platform_links: "平台协同",
  inherit_global: "继承全局", account_name: "账号名称", audience: "受众", role: "角色", extra_redlines: "额外红线",
  rhythm: "节奏", daily_quotas: "每日配额", passive_replies: "被动回复", active_comments: "主动评论", follows: "关注",
  windows: "时段", ops: "运营", draft_save: "存草稿", start_hour: "开始", end_hour: "结束",
  circuit_breaker: "熔断", cooldown_minutes: "冷却分钟", hot_topics: "热点", filters: "门限",
  heat_threshold: "热度门槛", heat_filter_enabled: "热度门开关", time_window_hours: "时效窗口", time_filter_enabled: "时效门开关",
  scheduler: "定时任务", jobs: "", enabled: "启用", interval_minutes: "间隔分钟", cron: "执行时间",
  kb_scan: "选题库扫描", daily_ops: "每日运营", hot_topics_refresh: "热点采集", autogrow: "自动闭环",
  platforms: "平台", xhs: "小红书", douyin: "抖音", kuaishou: "快手", wechat_channels: "视频号",
  llm: "模型", model: "模型", provider: "服务商", timeouts: "超时", scoring: "评分权重", topic_kb: "选题库", path: "文件位置",
};
function configKeyLabel(key: string): string {
  return key.split(".").map((p) => KEY_PART[p] ?? p).filter(Boolean).join(" · ");
}

function _str(v: number | null | undefined): string {
  return v == null ? "" : String(v);
}

/** 通知与访问：改口令 / 退出登录 / 企微飞书 Webhook（URL 只存 .env，不回显） */
function AccessNotifyPanel({ view, onSaved }: { view: SystemConfigView; onSaved: () => void }) {
  const { auth, refreshAuth, showToast } = useApp();
  const [password, setPassword] = useState("");
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [wecom, setWecom] = useState("");
  const [feishu, setFeishu] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const channels = view.readonly.notification_channels || {};
  const macEnabled = Boolean(channels.macos?.enabled);
  const macAvailable = channels.macos?.available !== false;

  const changePassword = async () => {
    if (password.trim().length < 8) {
      showToast("访问口令至少 8 个字符，并混合至少三类字符", "error");
      return;
    }
    setBusy("password");
    try {
      await api.setPassword(password, bootstrapToken);
      setPassword("");
      setBootstrapToken("");
      showToast("访问口令已更新，请重新登录", "success");
      await refreshAuth();
    } catch (e) {
      showToast("操作失败，请稍后重试。", "error");
    } finally {
      setBusy(null);
    }
  };

  const logout = async () => {
    await api.authLogout();
    await refreshAuth();
  };

  const saveChannel = async (channel: "wecom" | "feishu") => {
    const url = channel === "wecom" ? wecom : feishu;
    if (!url.trim()) {
      showToast("请填写 Webhook 地址", "error");
      return;
    }
    setBusy(channel);
    try {
      await api.saveWebhook(channel, url.trim());
      (channel === "wecom" ? setWecom : setFeishu)("");
      showToast(`${channel === "wecom" ? "企微" : "飞书"} Webhook 已保存到 .env`, "success");
      onSaved();
    } catch (e) {
      showToast("操作失败，请稍后重试。", "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="panel" style={{ marginTop: 18 }}>
      <div className="panel-head">
        <div>
          <div className="panel-title"><span className="icon">◈</span>通知与访问</div>
          <div className="panel-sub">访问口令保护页面和接口；Webhook 地址只写入 .env，保存后不再回显</div>
        </div>
      </div>

      <div className="grid two">
        <div>
          <div className="field-label">访问口令</div>
          <div className="note" style={{ marginBottom: 8 }}>
            {auth?.configured
              ? "已设置口令，接口需要登录后才能调用"
              : "首次设置仅允许本机直连；公网域名或反向代理部署需先在服务器配置 ITERWAVE_BOOTSTRAP_TOKEN，再在此输入引导口令。"}
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              className="field" type="password" placeholder="新口令（至少 8 位，混合大小写字母、数字和符号）"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
            {!auth?.configured && (
              <input
                className="field" type="password" placeholder="首次设置引导口令（公网部署）"
                value={bootstrapToken} onChange={(e) => setBootstrapToken(e.target.value)}
              />
            )}
            <button className="btn sm" type="button" disabled={busy === "password"} onClick={changePassword}>
              {auth?.configured ? "修改口令" : "设置口令"}
            </button>
            {auth?.configured && (
              <button className="btn sm soft" type="button" onClick={logout}>退出登录</button>
            )}
          </div>
        </div>

        <div>
          <div className="field-label">消息通知</div>
          <div className="note" style={{ marginBottom: 8 }}>
            {macAvailable
              ? `macOS 通知${macEnabled ? "已开启" : "未开启"}；跨平台部署请用 Webhook`
              : "当前服务部署环境不提供 macOS 本机通知，请使用企微或飞书 Webhook"}
          </div>
          {(["wecom", "feishu"] as const).map((channel) => {
            const info = channels[channel];
            const value = channel === "wecom" ? wecom : feishu;
            const setValue = channel === "wecom" ? setWecom : setFeishu;
            return (
              <div key={channel} style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                <input
                  className="field" style={{ flex: 1, minWidth: 160 }}
                  placeholder={channel === "wecom" ? "企微机器人 Webhook" : "飞书机器人 Webhook"}
                  value={value} onChange={(e) => setValue(e.target.value)}
                />
                <button className="btn sm soft" type="button"
                  disabled={busy === channel} onClick={() => saveChannel(channel)}>
                  {info?.has_webhook_url ? "更新" : "保存"}
                </button>
                <span className={info?.has_webhook_url ? "pill ok" : "pill"}>
                  {info?.has_webhook_url ? "已配置" : "未配置"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

