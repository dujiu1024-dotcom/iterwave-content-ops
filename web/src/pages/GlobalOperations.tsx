import { useCallback, useEffect, useState } from "react";
import { api, GlobalMonitorResponse, GlobalTaskQueueItem } from "../api";
import { navigate } from "../nav";

const STATUS: Record<string, string> = {
  queued: "排队中", running: "运行中", succeeded: "已完成",
  failed: "失败", cancelled: "已取消", interrupted: "中断",
};
const PLATFORMS: Record<string, string> = {
  xhs: "小红书", douyin: "抖音", kuaishou: "快手", wechat_channels: "视频号",
};

export default function GlobalOperations() {
  const [monitor, setMonitor] = useState<GlobalMonitorResponse | null>(null);
  const [tasks, setTasks] = useState<GlobalTaskQueueItem[]>([]);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  const [failedCount, setFailedCount] = useState<number | null>(null);
  const [failedTasks, setFailedTasks] = useState<GlobalTaskQueueItem[]>([]);
  const [activePage, setActivePage] = useState(1);
  const [failedPage, setFailedPage] = useState(1);
  const [activePages, setActivePages] = useState(1);
  const [failedPages, setFailedPages] = useState(1);
  const [activeCounts, setActiveCounts] = useState<{ queued: number; running: number }>({ queued: 0, running: 0 });
  const [tenantCounts, setTenantCounts] = useState<Record<string, { queued: number; running: number; failed: number }>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    const results = await Promise.allSettled([
      api.globalOperationsSummary(30),
      api.taskQueue({ scope: "active", page: activePage, pageSize: 12 }),
      api.taskQueue({ scope: "failed", page: failedPage, pageSize: 12 }),
    ]);
    if (results[0].status === "fulfilled") setMonitor(results[0].value);
    if (results[1].status === "fulfilled") {
      const data = results[1].value;
      setTasks(data.items ?? []);
      setActiveCount(data.total ?? data.items?.length ?? 0);
      setActivePages(data.pages ?? 1);
      if (activePage > (data.pages ?? 1)) setActivePage(data.pages ?? 1);
      setActiveCounts({
        queued: data.status_counts?.queued ?? 0,
        running: data.status_counts?.running ?? 0,
      });
    }
    if (results[2].status === "fulfilled") {
      const data = results[2].value;
      setFailedCount(data.total ?? 0);
      setFailedTasks(data.items ?? []);
      setFailedPages(data.pages ?? 1);
      if (failedPage > (data.pages ?? 1)) setFailedPage(data.pages ?? 1);
    }
    const activeByTenant = results[1].status === "fulfilled" ? results[1].value.tenant_status_counts ?? {} : {};
    const failedByTenant = results[2].status === "fulfilled" ? results[2].value.tenant_status_counts ?? {} : {};
    const tenantIds = new Set([...Object.keys(activeByTenant), ...Object.keys(failedByTenant)]);
    setTenantCounts(Object.fromEntries(Array.from(tenantIds, (tenantId) => [
      tenantId,
      {
        queued: activeByTenant[tenantId]?.queued ?? 0,
        running: activeByTenant[tenantId]?.running ?? 0,
        failed: (failedByTenant[tenantId]?.failed ?? 0) + (failedByTenant[tenantId]?.interrupted ?? 0),
      },
    ])));
    const failures = results.flatMap((r, i) => r.status === "rejected"
      ? [`${["账号组汇总", "活跃任务", "失败任务统计"][i]}：${r.reason instanceof Error ? r.reason.message : String(r.reason)}`]
      : []);
    setError(failures.join("；"));
    setUpdatedAt(new Date());
    setLoading(false);
  }, [activePage, failedPage]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const active = tasks;
  const failed = failedTasks;
  return (
    <div className="page global-operations-page">
      <div className="page-head">
        <div>
          <div className="eyebrow">GLOBAL OPERATIONS</div>
          <h1 className="page-title">全局运营中心</h1>
          <p className="page-desc">跨账号组独立总览，不受当前账号组切换影响。任务状态来自全局队列，内容统计来自各账号组数据库。</p>
          {updatedAt && <div className="tiny">更新于 {updatedAt.toLocaleTimeString("zh-CN")}</div>}
        </div>
        <div className="head-actions">
          <button className="btn sm" onClick={() => void refresh()} disabled={loading}>{loading ? "刷新中…" : "↻ 刷新"}</button>
          <button className="btn sm" onClick={() => navigate("task-queue")}>查看任务队列</button>
        </div>
      </div>

      {error && <div className="panel" role="alert" style={{ borderColor: "var(--orange)", color: "var(--orange)" }}>
        部分数据加载失败：{error}。未成功加载的指标不会按 0 处理。
      </div>}

      <div className="grid four" style={{ marginBottom: 16 }}>
        <div className="metric"><div className="metric-label">账号组</div><div className="metric-val">{monitor?.totals?.tenants ?? "—"}</div><div className="metric-foot">数据库可读取 {monitor?.totals?.available_tenants ?? "—"} 个</div></div>
        <div className="metric"><div className="metric-label">运行中任务</div><div className="metric-val">{activeCount == null ? "—" : activeCounts.running}</div><div className="metric-foot">来自全局队列数据库聚合</div></div>
        <div className="metric"><div className="metric-label">等待任务</div><div className="metric-val">{activeCount == null ? "—" : activeCounts.queued}</div><div className="metric-foot">按全局 FIFO 入队顺序</div></div>
        <div className="metric"><div className="metric-label">失败 / 中断</div><div className="metric-val">{failedCount ?? "—"}</div><div className="metric-foot">失败/中断记录（总数由数据库计数）</div></div>
      </div>

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="panel-head">
          <div><div className="panel-title">账号组运行矩阵</div><div className="panel-sub">账号组数据库、真实登录态、调度器和全局队列分布；查看详情不会切换当前账号组。</div></div>
          <span className="muted">{monitor?.tenants?.length ?? "—"} 个账号组</span>
        </div>
        {loading && !monitor ? <div className="empty-state">正在汇总账号组…</div> : !monitor ? <div className="empty-state">账号组数据暂不可用。</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>账号组</th><th>数据库</th><th>平台账号</th><th>平台登录</th><th>运行中</th><th>排队中</th><th>失败 / 中断</th><th>定时调度</th><th>活跃轮次</th><th>操作</th></tr></thead>
              <tbody>{(monitor.tenants ?? []).map((tenant) => {
                const q = tenantCounts[tenant.tenant_id] ?? { queued: 0, running: 0, failed: 0 };
                return <tr key={tenant.tenant_id}>
                  <td><strong>{tenant.tenant_name}</strong><div className="tiny">{tenant.tenant_id}</div></td>
                  <td><span className={tenant.db_status === "uninitialized" ? "tag gray" : tenant.db_available ? "tag green" : "tag red"}>{tenant.db_status === "uninitialized" ? "未初始化" : tenant.db_available ? "可读取" : "不可用"}</span></td>
                  <td>{tenant.registered_accounts_active ?? tenant.registered_accounts ?? "—"} / {tenant.registered_accounts ?? "—"}</td>
                  <td>{tenant.runtime?.available ? (
                    <div className="global-platform-cell">
                      <strong>{(tenant.runtime.logged_in_platforms ?? []).length}/{(tenant.runtime.enabled_platforms ?? []).length} 已登录</strong>
                      <span className="tiny">已登录：{(tenant.runtime.logged_in_platforms ?? []).map((p) => PLATFORMS[p] ?? p).join("、") || "无"}</span>
                      <span className="tiny">未登录：{(tenant.runtime.platforms_without_login ?? []).map((p) => PLATFORMS[p] ?? p).join("、") || "无"}</span>
                      {(tenant.runtime.circuit_broken_platforms ?? []).length > 0 && <span className="tiny" style={{ color: "var(--red)" }}>熔断：{(tenant.runtime.circuit_broken_platforms ?? []).map((p) => PLATFORMS[p] ?? p).join("、")}</span>}
                    </div>
                  ) : tenant.db_status === "uninitialized" ? <span className="tag gray">未初始化</span> : tenant.db_available ? <span className="tag gray">未启动</span> : <span className="tag red">数据库不可用</span>}</td>
                  <td>{q.running}</td><td>{q.queued}</td><td>{q.failed}</td>
                  <td>{tenant.runtime?.available ? (
                    <div className="global-scheduler-cell">
                      <span className={tenant.runtime.scheduler_running ? "tag green" : "tag red"}>{tenant.runtime.scheduler_running ? "运行中" : "已停止"}</span>
                      <span className="tiny">启用 {tenant.runtime.scheduler_enabled_count ?? "—"} / 注册 {tenant.runtime.scheduler_job_count ?? "—"}</span>
                    </div>
                  ) : tenant.db_status === "uninitialized" ? <span className="tag gray">未初始化</span> : tenant.db_available ? <span className="tag gray">未启动</span> : <span className="tag gray">—</span>}</td>
                  <td>{tenant.runtime?.active_rounds ?? "—"}</td>
                  <td><span className="muted">—</span></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid two">
        <section className="panel">
          <div className="panel-head"><div><div className="panel-title">全局 FIFO 任务</div><div className="panel-sub">运行任务不会因账号组切换而中断。</div></div><button className="btn sm" onClick={() => navigate("task-queue")}>完整队列</button></div>
          {active.length ? <div className="list">{active.map((task) => <div className="task-row" key={task.task_id}>
            <i className={`task-prio ${task.status === "running" ? "green" : "blue"}`} />
            <div className="task-main"><div className="task-title">{task.tenant_name} · {task.task_type === "round_run" ? "内容生产" : task.task_type}</div><div className="task-desc">#{task.queue_order} · {STATUS[task.status]} · {task.aggregate_id ?? "—"}</div></div>
          </div>)}</div> : <div className="empty-state">当前没有排队或执行中的任务。</div>}
          {activePages > 1 && <div className="pagination-row" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, paddingTop: 12 }}>
            <button className="btn sm" disabled={activePage <= 1 || loading} onClick={() => setActivePage((p) => Math.max(1, p - 1))}>上一页</button>
            <span className="tiny">第 {activePage} / {activePages} 页 · 共 {activeCount ?? 0} 项</span>
            <button className="btn sm" disabled={activePage >= activePages || loading} onClick={() => setActivePage((p) => Math.min(activePages, p + 1))}>下一页</button>
          </div>}
        </section>
        <section className="panel">
          <div className="panel-head"><div><div className="panel-title">异常与待处理</div><div className="panel-sub">全量队列中的失败或中断任务；总数与账号组分布不受当前页影响。</div></div><span className="tag">{failedCount ?? "—"} 项</span></div>
          {failed.length ? <div className="list">{failed.map((task) => <div className="task-row" key={task.task_id}>
            <i className="task-prio red" /><div className="task-main"><div className="task-title">{task.tenant_name} · {STATUS[task.status]}</div><div className="task-desc">{task.error_message || task.task_type}</div></div>
          </div>)}</div> : <div className="empty-state">当前没有失败或中断任务。</div>}
          {failedPages > 1 && <div className="pagination-row" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, paddingTop: 12 }}>
            <button className="btn sm" disabled={failedPage <= 1 || loading} onClick={() => setFailedPage((p) => Math.max(1, p - 1))}>上一页</button>
            <span className="tiny">第 {failedPage} / {failedPages} 页 · 共 {failedCount ?? 0} 项</span>
            <button className="btn sm" disabled={failedPage >= failedPages || loading} onClick={() => setFailedPage((p) => Math.min(failedPages, p + 1))}>下一页</button>
          </div>}
        </section>
      </div>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head"><div><div className="panel-title">平台与内容汇总</div><div className="panel-sub">近 30 天跨账号组内容产出；账号组登录态与调度状态来自各自运行时快照。</div></div></div>
        <div className="grid four">
          {Object.entries(monitor?.platform_totals ?? {}).map(([platform, value]) => <div className="metric" key={platform}>
            <div className="metric-label">{PLATFORMS[platform] ?? platform}</div><div className="metric-val">{monitor?.tenants?.reduce((sum, tenant) => sum + (tenant.platforms?.[platform]?.contents_period ?? 0), 0) ?? "—"}</div><div className="metric-foot">近 30 天内容 · 草稿 {value.drafts}</div>
          </div>)}
        </div>
        {!monitor && <div className="empty-state">等待汇总数据。</div>}
      </section>
    </div>
  );
}
