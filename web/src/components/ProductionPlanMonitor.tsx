import { useCallback, useEffect, useState } from "react";
import { api, ProductionMonitorResponse, ProductionMonitorStage } from "../api";
import { navigate } from "../nav";

const PLATFORM_LABEL: Record<string, string> = {
  xhs: "小红书", douyin: "抖音", kuaishou: "快手", wechat_channels: "视频号",
};
const STATUS_LABEL: Record<string, string> = {
  draft: "草稿", active: "执行中", paused: "已暂停", completed: "已完成",
  pending: "待执行", in_progress: "执行中", cancelled: "已取消",
  created: "已创建", producing: "生产中", awaiting_publish: "待审核发布",
  collecting_metrics: "数据采集中", analyzing: "复盘中", partial_completed: "部分完成",
  failed: "失败", succeeded: "成功",
};

function Metric({ label, value }: { label: string; value: number | undefined }) {
  return <div className="metric"><div className="metric-label">{label}</div><div className="metric-val">{value ?? "—"}</div></div>;
}

function Stage({ stage }: { stage: ProductionMonitorStage }) {
  return <div className="panel" style={{ marginTop: 10, padding: 12 }}>
    <div className="panel-head">
      <div>
        <strong>{stage.stage_order}. {stage.title}</strong>
        <div className="tiny">{STATUS_LABEL[stage.status] ?? stage.status} · 目标 {stage.target_count} 篇 · {stage.round_count} 轮</div>
      {stage.direction && <div className="tiny">执行方向：{stage.direction}</div>}
      {stage.completion_condition && <div className="tiny">完成条件：{stage.completion_condition}</div>}
      </div>
      <span className="tag">{stage.production_task_count} 个生产任务</span>
    </div>
    <div className="tiny" style={{ marginTop: 6 }}>
      前置依赖：{stage.dependencies_available ? (stage.dependencies?.length ? stage.dependencies.join("、") : "无") : "当前策略步骤模型尚未持久化依赖字段，需由策略模型 PR 补齐"}
    </div>
    <div className="grid four" style={{ gap: 8, marginTop: 10 }}>
      <Metric label="内容成品" value={stage.content_count}/>
      <Metric label="待审核" value={stage.review_pending_count}/>
      <Metric label="审核通过" value={stage.review_approved_count}/>
      <Metric label="已确认发布" value={stage.published_count}/>
      <Metric label="失败草稿" value={stage.failed_draft_count}/>
    </div>
    {stage.review_rejected_count > 0 && <div className="tiny" style={{ color: "var(--orange)", marginTop: 8 }}>人工驳回 {stage.review_rejected_count} 篇</div>}
    {stage.contents?.length > 0 && <div style={{ marginTop: 12 }}>
      <div className="tiny" style={{ fontWeight: 700, marginBottom: 6 }}>最近内容成品（{stage.content_count} 篇）</div>
      <div className="list">
        {stage.contents.map((content) => <div className="task-row" key={content.id}>
          <div className="task-main">
            <div className="task-title">{content.title}</div>
            <div className="task-desc">{content.id} · {PLATFORM_LABEL[content.platform] ?? content.platform} · {content.draft_count} 个草稿 · {content.review_rejected ? "已驳回" : content.draft_statuses || "无草稿状态"}</div>
          </div>
          <button className="btn sm" onClick={() => navigate("production", "pipeline", { type: "content", id: content.id })}>查看内容</button>
        </div>)}
      </div>
    </div>}
    {stage.rounds.length > 0 && <div className="list" style={{ marginTop: 8 }}>
      {stage.rounds.map((round) => <div className="task-row" key={round.id}>
        <div className="task-main">
          <div className="task-title">第 {round.round_number} 轮 · {STATUS_LABEL[round.status] ?? round.status}</div>
          <div className="task-desc">轮次 #{round.id} · {round.created_at ? new Date(round.created_at).toLocaleString("zh-CN") : "时间未知"} · {round.trigger_source ?? "来源未知"}</div>
        </div>
        <button className="btn sm" onClick={() => navigate("production", "rounds", { type: "round", id: round.id })}>查看轮次</button>
        <button className="btn sm" onClick={() => navigate("task-queue")}>查看任务队列</button>
      </div>)}
    </div>}
    {stage.round_count > stage.rounds.length && <div className="tiny" style={{ marginTop: 6 }}>仅展示最近 {stage.rounds.length} 轮；共 {stage.round_count} 轮。</div>}
    <div className="tiny" style={{ marginTop: 6 }}>轮次状态：{Object.entries(stage.round_status_counts ?? {}).filter(([, count]) => count > 0).map(([status, count]) => `${STATUS_LABEL[status] ?? status} ${count}`).join(" · ") || "暂无轮次"}</div>
  </div>;
}

export default function ProductionPlanMonitor() {
  const [data, setData] = useState<ProductionMonitorResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setData(await api.productionMonitor()); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  return <section className="panel" style={{ marginBottom: 16 }}>
    <div className="panel-head">
      <div><div className="panel-title">母计划 · 平台策略生产监控</div>
        <div className="panel-sub">从当前生效母计划下钻到平台子计划、策略步骤、生产轮次和内容成品；只读真实业务数据。</div>
      </div>
      <button className="btn sm" onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "刷新生产监控"}</button>
    </div>
    {error && <div role="alert" className="empty-state">生产监控加载失败：{error}</div>}
    {loading && !data && <div className="empty-state">正在读取计划与生产数据…</div>}
    {data && <>
      <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
        {data.master_plan ? <>
          <div className="panel-title">当前母计划 V{data.master_plan.version_no}</div>
          <div className="tiny">版本 #{data.master_plan.id} · 生效于 {data.master_plan.effective_at ? new Date(data.master_plan.effective_at).toLocaleString("zh-CN") : "时间未知"}</div>
        </> : <div className="empty-state">当前没有生效中的母计划。</div>}
        <div className="grid four" style={{ gap: 8, marginTop: 12 }}>
          <Metric label="平台子计划" value={data.totals.subplans}/>
          <Metric label="策略步骤" value={data.totals.stages}/>
          <Metric label="生产轮次" value={data.totals.rounds}/>
          <Metric label="成功轮次" value={data.totals.successful_rounds}/>
          <Metric label="生产任务" value={data.totals.production_tasks}/>
          <Metric label="内容成品" value={data.totals.contents}/>
          <Metric label="目标内容完成率（%）" value={data.totals.content_progress_percent ?? undefined}/>
          <Metric label="待审核" value={data.totals.review_pending}/>
          <Metric label="审核通过" value={data.totals.review_approved}/>
          <Metric label="审核驳回" value={data.totals.review_rejected}/>
          <Metric label="已确认发布" value={data.totals.published}/>
          <Metric label="异常轮次" value={data.totals.abnormal_rounds}/>
          <Metric label="失败轮次 / 草稿" value={(data.totals.failed_rounds ?? 0) + (data.totals.failed_drafts ?? 0)}/>
        </div>
      </div>
      <div className="panel" style={{ padding: 12, marginBottom: 12 }}>
        <div className="panel-title">内容效果汇总</div>
        <div className="grid four" style={{ gap: 8, marginTop: 10 }}>
          <Metric label="浏览量" value={data.effect_summary.views ?? undefined}/>
          <Metric label="点赞" value={data.effect_summary.likes ?? undefined}/>
          <Metric label="收藏" value={data.effect_summary.collects ?? undefined}/>
          <Metric label="评论" value={data.effect_summary.comments ?? undefined}/>
          <Metric label="分享" value={data.effect_summary.shares ?? undefined}/>
          <Metric label="新增关注" value={data.effect_summary.new_followers ?? undefined}/>
        </div>
        <div className="tiny" style={{ marginTop: 6 }}>仅汇总已采集指标；没有可用数值时显示 —，不把未知值当作 0。</div>
      </div>
      {data.consistency_checks.length > 0 && <div className="panel" style={{ borderColor: "var(--orange)", padding: 12, marginBottom: 12 }}>
        <strong>异常与一致性检查 · {data.consistency_checks.length} 项</strong>
        {data.consistency_checks.map((item, index) => <div className="task-row" key={`${item.code}-${index}`}>
          <i className={`task-prio ${item.severity === "critical" ? "red" : "blue"}`}/>
          <div className="task-main"><div className="task-title">{item.message}</div><div className="task-desc">{item.code}{item.round_id ? ` · 轮次 #${item.round_id}` : ""}{item.count ? ` · ${item.count} 条` : ""}</div></div>
          {item.round_id && <button className="btn sm" onClick={() => navigate("production", "rounds", { type: "round", id: item.round_id! })}>定位轮次</button>}
        </div>)}
      </div>}
      {data.platforms.map((subplan) => {
        const currentStage = subplan.stages.find((stage) => stage.status === "in_progress")
          ?? subplan.stages.find((stage) => stage.status === "pending");
        return <div className="panel" style={{ padding: 12, marginTop: 12 }} key={subplan.id}>
        <div className="panel-head">
          <div><div className="panel-title">{PLATFORM_LABEL[subplan.platform] ?? subplan.platform} · {subplan.title}</div>
            <div className="panel-sub">子计划 {subplan.id} · V{subplan.version_no ?? "—"} · {STATUS_LABEL[subplan.status] ?? subplan.status} · {subplan.stage_count} 个策略步骤 · {subplan.round_count} 轮 · 当前执行：{currentStage?.title ?? "暂无待执行步骤"}</div>
          </div>
          <button className="btn sm" onClick={() => navigate("production", "pipeline")}>查看生产线</button>
        </div>
        {subplan.stages.length ? subplan.stages.map((stage) => <Stage key={stage.id} stage={stage}/>) : <div className="empty-state">该子计划当前版本没有策略步骤。</div>}
      </div>;
      })}
      <div className="tiny" style={{ marginTop: 10 }}>统计口径：发布数仅统计 user_confirmed_at；生产任务来自 task_runs.round_pipeline。指标采集记录 {data.totals.metric_records ?? 0} 条。{!data.limitations.task_model_available ? "当前数据库没有 task_runs，生产任务数按真实可查记录展示为 0，不以轮次数冒充任务数。" : ""}</div>
      <div className="tiny">数据范围：当前账号组的生效母计划 · 更新于 {new Date(data.generated_at).toLocaleString("zh-CN")}</div>
    </>}
  </section>;
}
