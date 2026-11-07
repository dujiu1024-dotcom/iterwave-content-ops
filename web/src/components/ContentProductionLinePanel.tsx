import { useCallback, useEffect, useState } from "react";
import { api, type ContentProductionLineRecord } from "../api";
import { useApp } from "../AppContext";
import { displayStatusLabel, displayErrorLabel, displayPlatformLabel } from "../displayLabels";
import { navigate } from "../nav";

const LINE_STATUS: Record<string, string> = {
  pending: "待生产", producing: "生产中", review_pending: "待审核",
  awaiting_save: "待存稿", awaiting_publish: "待发布",
  verification_pending: "待核验", published: "已核验发布",
  closed: "已关闭", failed: "失败", recovering: "恢复中",
  aborted: "已中止", legacy_mapped: "历史任务映射",
};
const ACTIVE = new Set(["pending","producing","review_pending","awaiting_save","awaiting_publish","verification_pending","published","failed","recovering"]);
function dateLabel(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

export default function ContentProductionLinePanel() {
  const { showToast } = useApp();
  const [items, setItems] = useState<ContentProductionLineRecord[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const result = await api.contentProductionLines(undefined, 50);
      setItems(result.items ?? []);
      setSelectedId((current) => current && result.items.some((item) => item.id === current)
        ? current : result.items.find((item) => ACTIVE.has(item.status))?.id ?? result.items[0]?.id ?? "");
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const selected = items.find((item) => item.id === selectedId) ?? null;
  const context = selected?.context_snapshot?.context;
  const decision = context?.task_decision_input as any;
  const topic = decision?.topic as Record<string, unknown> | undefined;
  const stage = context?.sub_goal as Record<string, unknown> | undefined;
  const dependencyOutcomes = (stage?.dependency_outcomes ?? {}) as Record<string, any>;
  const versions = context?.plan_versions ?? {};
  const businessGaps = context?.business_gaps ?? [];
  const resourceGaps = context?.resource_gaps ?? [];

  const recover = async () => {
    if (!selected || selected.task_status !== "failed") return;
    const confirmed = window.confirm("请确认本任务没有尚未核实的平台外部副作用（例如已提交但未回读的发布/存稿操作）。确认后才允许恢复。");
    if (!confirmed) return;
    setBusy(true);
    try {
      await api.recoverProductionTask(selected.production_task_id, true);
      showToast("已提交生产任务恢复请求", "success");
      await load();
    } catch (err) { showToast("恢复失败：" + (err instanceof Error ? err.message : String(err)), "error"); }
    finally { setBusy(false); }
  };

  return <section className="panel" style={{ marginBottom: 16 }}>
    <div className="panel-head">
      <div><div className="panel-title">单内容生产线</div>
        <div className="panel-sub">一条正式内容任务对应一条独立生产线；上一条完成审核、核验并关闭后，才允许启动下一条。</div></div>
      <button className="btn sm" onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "刷新"}</button>
    </div>
    {error && <div className="note" role="alert">生产线读取失败：{error}</div>}
    {loading && items.length === 0 && <div className="note">正在读取单内容生产线…</div>}
    {!loading && items.length === 0 && !error && <div className="note">尚无单内容生产线。新生产任务创建时会自动生成生产线和经营上下文快照。</div>}
    {items.length > 0 && <>
      <div className="toolbar" style={{ marginBottom: 12 }}>
        <label className="tiny" htmlFor="content-production-line-select">生产任务</label>
        <select id="content-production-line-select" className="field" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} style={{ maxWidth: 420 }}>
          {items.map((item) => <option key={item.id} value={item.id}>{LINE_STATUS[item.status] ?? displayStatusLabel(item.status)} · {displayPlatformLabel(item.platform)}</option>)}
        </select>
      </div>
      {selected && <>
        <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
          <div className="panel-head">
            <div><div className="panel-title">{LINE_STATUS[selected.status] ?? displayStatusLabel(selected.status)}</div>
              <div className="panel-sub">{displayPlatformLabel(selected.platform)} · 单内容生产任务</div></div>
            <span className={"tag " + (selected.status === "failed" ? "red" : ACTIVE.has(selected.status) ? "orange" : "green")}>{LINE_STATUS[selected.status] ?? selected.status}</span>
          </div>
          <div className="grid four" style={{ gap: 8 }}>
            <div className="kpi-card"><div className="metric-label">执行尝试</div><div className="metric-val">{selected.attempt_count}/{selected.max_attempts}</div></div>
            <div className="kpi-card"><div className="metric-label">母计划版本</div><div className="metric-val">{String(versions.master_plan_version_no ?? "—")}</div></div>
            <div className="kpi-card"><div className="metric-label">可用素材</div><div className="metric-val">{context?.available_original_assets?.length ?? "—"}</div></div>
            <div className="kpi-card"><div className="metric-label">资源缺口</div><div className="metric-val">{context?.resource_gaps?.length ?? "—"}</div></div>
          </div>
          {selected.content_id && <div className="task-row" style={{ marginTop: 10 }}>
            <div className="task-main"><div className="task-title">内容成品 {selected.content_id}</div></div>
            <button className="btn sm" onClick={() => navigate("production", "pipeline", { type: "content", id: selected.content_id! })}>查看成品</button>
          </div>}
          {selected.task_status === "failed" && <>
            <div className="note" role="alert" style={{ marginTop: 10 }}>{displayErrorLabel(selected.error_code ?? undefined)}（{selected.error_code || "未知错误"}）：{selected.error_message || "未提供错误信息"}</div>
            <button className="btn primary" style={{ marginTop: 8 }} disabled={busy} onClick={() => void recover()}>{busy ? "提交中…" : "确认无外部副作用并恢复任务"}</button>
          </>}
        </div>
        <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
          <div className="panel-title">生产决策与经营上下文快照</div>
          {!selected.context_snapshot ? <div className="note">快照尚未生成。该任务不可领取执行，需检查生产上下文组装错误。</div> : <>
            <div className="tiny" style={{ marginTop: 6 }}>快照 {selected.context_snapshot.id} · {dateLabel(selected.context_snapshot.captured_at)} · 状态 {selected.context_snapshot.context_status === "complete" ? "完整" : selected.context_snapshot.context_status === "waived" ? "人工豁免" : "历史不完整"}</div>
            {selected.context_snapshot.context_status === "legacy_incomplete" && <div className="note" role="alert">这是迁移前历史任务的输入快照，不代表已具备完整经营上下文；不会被当作新任务的生产依据。</div>}
            <div className="grid two" style={{ gap: 8, marginTop: 10 }}>
              <div className="kpi-card"><div className="metric-label">北极星目标</div><div className="metric-val" style={{ fontSize: 13 }}>{context?.north_star?.goal_text ?? "缺失/已豁免"}</div></div>
              <div className="kpi-card"><div className="metric-label">当前子目标</div><div className="metric-val" style={{ fontSize: 13 }}>{String(stage?.title ?? "—")}</div></div>
              <div className="kpi-card"><div className="metric-label">当前选题</div><div className="metric-val" style={{ fontSize: 13 }}>{String(topic?.title ?? "—")}</div></div>
              <div className="kpi-card"><div className="metric-label">最近复盘引用</div><div className="metric-val" style={{ fontSize: 13 }}>{context?.latest_review_ref ?? "暂无历史复盘"}</div></div>
            </div>
            <div className="tiny" style={{ marginTop: 10 }}>母计划 V{String(versions.master_plan_version_no ?? "—")} · 子计划 V{String(versions.sub_plan_version_no ?? "—")} · 策略步骤 {String(versions.strategy_step_id ?? "—")} · 诊断报告 V{String(context?.business_diagnosis?.version_no ?? "—")}</div>
            <div className="tiny" style={{ marginTop: 6 }}>前置依赖：{Array.isArray(stage?.dependencies) && stage!.dependencies.length ? (stage!.dependencies as string[]).join("、") : "无"} · 依赖验收：{Object.keys(dependencyOutcomes).length ? Object.entries(dependencyOutcomes).map(([key, value]) => key + "：" + String(value?.status ?? value?.decision ?? "已通过")).join("；") : "无前置依赖"} · 最近已发布内容：{context?.recent_published_content?.title ?? "暂无已发布内容"} · 指标记录：{context?.recent_published_content?.metrics?.length ?? 0} 条 · 发布核验：{context?.recent_published_content?.verification_status === "verified" ? "已核验" : context?.recent_published_content?.verification_status === "legacy_unverified" ? "历史未核验" : "暂无"}</div>
            <div className="tiny" style={{ marginTop: 6 }}>经营缺口：{businessGaps.length} 项 · 平台分析：{context?.account_performance?.platform_insights?.length ?? 0} 组 · 账号快照：{context?.account_performance?.account_snapshots?.length ?? 0} 条 · 内部资料：{context?.internal_materials?.length ?? 0} 条 · 外部引用：{context?.external_references?.length ?? 0} 条 · 素材版本：{context?.original_asset_versions?.length ?? 0} 个 · 素材关联：{context?.original_asset_relationships?.length ?? 0} 条</div>
            {businessGaps.length > 0 && <div className="note" style={{ marginTop: 8 }}>当前经营缺口：{businessGaps.slice(0, 3).map((gap) => typeof gap === "string" ? gap : String((gap as any).title ?? (gap as any).description ?? JSON.stringify(gap))).join("；")}</div>}
            {resourceGaps.length > 0 && <div className="note" style={{ marginTop: 8 }}>当前资源缺口：{resourceGaps.slice(0, 3).map((gap) => typeof gap === "string" ? gap : String((gap as any).title ?? (gap as any).description ?? JSON.stringify(gap))).join("；")}</div>}
            {context?.manual_waiver && <div className="note" style={{ marginTop: 8 }}>人工豁免：{context.manual_waiver.reason} · 操作人 {context.manual_waiver.actor} · 豁免项 {(context.manual_waiver.waived_items ?? []).join("、")}</div>}
          </>}
        </div>
        <div className="panel" style={{ padding: 12 }}>
          <div className="panel-title">生产状态与审计事件</div>
          <div className="list" style={{ marginTop: 8 }}>
            {selected.events.map((event) => <div className="task-row" key={event.id}>
              <div className="task-main"><div className="task-title">{event.event_type} · {event.from_status ?? "—"} → {event.to_status ?? "—"}</div><div className="task-desc">{dateLabel(event.created_at)} · {event.reason || "无补充说明"}</div></div>
            </div>)}
          </div>
        </div>
      </>}
    </>}
  </section>;
}
