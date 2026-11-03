import { useCallback, useEffect, useState } from "react";
import { api, GlobalMonitorResponse, GlobalTaskQueueItem, GlobalTodoItem, NotificationItem } from "../api";
import { navigate } from "../nav";
import { roundTaskLabel } from "../roundLabel";
import ProductionPlanMonitor from "../components/ProductionPlanMonitor";

const STATUS: Record<string, string> = {
  queued: "排队中", running: "运行中", succeeded: "已完成",
  failed: "失败", cancelled: "已取消", interrupted: "中断",
};
const TASK_TYPE_LABEL: Record<string, string> = {
  round_run: "内容生产", scheduler_job: "账号组后台任务",
};
const PLATFORMS: Record<string, string> = {
  xhs: "小红书", douyin: "抖音", kuaishou: "快手", wechat_channels: "视频号",
};

const RUN_STATUS: Record<string, string> = {
  pending: "待执行", running: "执行中", success: "成功", succeeded: "成功",
  failed: "失败", interrupted: "中断", skipped: "跳过", pending_verify: "待核实",
};
const RUN_TYPE: Record<string, string> = {
  publish_polling: "发布确认轮询", data_fetch: "数据采集", daily_ops: "每日运营",
  comment_polling: "评论轮询", cb_recovery: "熔断恢复", kb_scan: "选题库扫描",
  draft_save: "草稿保存", round_pipeline: "轮次流程", data_retention_cleanup: "数据清理",
};

export default function GlobalOperations() {
  const [monitor, setMonitor] = useState<GlobalMonitorResponse | null>(null);
  const [tasks, setTasks] = useState<GlobalTaskQueueItem[]>([]);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  const [failedCount, setFailedCount] = useState<number | null>(null);
  const [failedTasks, setFailedTasks] = useState<GlobalTaskQueueItem[]>([]);
  const [unreadNotifications, setUnreadNotifications] = useState<NotificationItem[]>([]);
  const [unreadNotificationCount, setUnreadNotificationCount] = useState<number | null>(null);
  const [todos, setTodos] = useState<GlobalTodoItem[]>([]);
  const [todoCount, setTodoCount] = useState(0);
  const [todoCounts, setTodoCounts] = useState<Record<string, number>>({});
  const [todoPage, setTodoPage] = useState(1);
  const [todoPages, setTodoPages] = useState(1);
  const [todoSource, setTodoSource] = useState<GlobalTodoItem["source"] | "all">("all");
  const [todoTenantId, setTodoTenantId] = useState("");
  const [todoCapped, setTodoCapped] = useState(false);
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
      api.notificationsPage(1, 5, "unread"),
      api.todos(todoPage, 12, todoTenantId.trim() || undefined, todoSource === "all" ? undefined : todoSource),
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
    if (results[3].status === "fulfilled") {
      setUnreadNotifications(results[3].value.items ?? []);
      setUnreadNotificationCount(results[3].value.unread_total ?? results[3].value.total ?? 0);
    }
    if (results[4].status === "fulfilled") {
      setTodos(results[4].value.items ?? []);
      setTodoCount(results[4].value.total ?? 0);
      setTodoCounts(results[4].value.counts ?? {});
      setTodoPages(results[4].value.pages ?? 1);
      setTodoCapped(Boolean(results[4].value.capped));
      if (todoPage > (results[4].value.pages ?? 1)) setTodoPage(results[4].value.pages ?? 1);
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
      ? [`${["账号组汇总", "活跃任务", "失败任务统计", "未读通知", "统一待办"][i]}：${r.reason instanceof Error ? r.reason.message : String(r.reason)}`]
      : []);
    setError(failures.join("；"));
    setUpdatedAt(new Date());
    setLoading(false);
  }, [activePage, failedPage, todoPage, todoSource, todoTenantId]);

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

      <ProductionPlanMonitor />

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
              <thead><tr><th>账号组</th><th>数据库</th><th>平台账号</th><th>平台登录</th><th>运行中</th><th>排队中</th><th>失败 / 中断</th><th>定时调度</th><th>活跃轮次</th><th>最近执行</th></tr></thead>
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
                  <td>{q.running}</td><td>{q.queued}</td><td>{q.failed || 0}</td>
                  <td>{tenant.runtime?.available ? (
                    <div className="global-scheduler-cell">
                      <span className={tenant.runtime.scheduler_running ? "tag green" : "tag red"}>{tenant.runtime.scheduler_running ? "运行中" : "已停止"}</span>
                      <span className="tiny">启用 {tenant.runtime.scheduler_enabled_count ?? "—"} / 注册 {tenant.runtime.scheduler_job_count ?? "—"}</span>
                    </div>
                  ) : tenant.db_status === "uninitialized" ? <span className="tag gray">未初始化</span> : tenant.db_available ? <span className="tag gray">未启动</span> : <span className="tag gray">—</span>}</td>
                  <td>{tenant.runtime?.active_rounds ?? "—"}</td>
                  <td>{tenant.recent_execution ? (
                    <div className="global-scheduler-cell">
                      <strong>{RUN_TYPE[tenant.recent_execution.task_type] ?? "其他后台任务"}</strong>
                      <span className={`tag ${tenant.recent_execution.status === "success" || tenant.recent_execution.status === "succeeded" ? "green" : tenant.recent_execution.status === "failed" || tenant.recent_execution.status === "interrupted" ? "red" : "gray"}`}>
                        {RUN_STATUS[tenant.recent_execution.status] ?? tenant.recent_execution.status}
                      </span>
                      {tenant.recent_execution.has_error_detail && <span className="tiny">诊断详情已记录</span>}
                      <span className="tiny">{tenant.recent_execution.finished_at || tenant.recent_execution.started_at || tenant.recent_execution.created_at
                        ? new Date(tenant.recent_execution.finished_at || tenant.recent_execution.started_at || tenant.recent_execution.created_at!).toLocaleString("zh-CN")
                        : "时间未知"}</span>
                    </div>
                  ) : <span className="muted">暂无执行记录</span>}</td>
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
            <div className="task-main"><div className="task-title">{task.tenant_name} · {task.task_type === "round_run" ? roundTaskLabel(task) : (TASK_TYPE_LABEL[task.task_type] ?? "账号组后台任务")}</div><div className="task-desc">队列 #{task.queue_order} · {STATUS[task.status]}</div></div>
          </div>)}</div> : <div className="empty-state">当前没有排队或执行中的任务。</div>}
          {activePages > 1 && <div className="pagination-row" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, paddingTop: 12 }}>
            <button className="btn sm" disabled={activePage <= 1 || loading} onClick={() => setActivePage((p) => Math.max(1, p - 1))}>上一页</button>
            <span className="tiny">第 {activePage} / {activePages} 页 · 共 {activeCount ?? 0} 项</span>
            <button className="btn sm" disabled={activePage >= activePages || loading} onClick={() => setActivePage((p) => Math.min(activePages, p + 1))}>下一页</button>
          </div>}
        </section>
        <section className="panel">
          <div className="panel-head"><div><div className="panel-title">异常与待处理</div><div className="panel-sub">当前返回的队列记录中失败或中断的任务。</div></div><span className="tag">{failedCount ?? "—"} 项</span></div>
          {failed.length ? <div className="list">{failed.map((task) => <div className="task-row" key={task.task_id}>
            <i className="task-prio red" /><div className="task-main"><div className="task-title">{task.tenant_name} · {task.task_type === "round_run" ? roundTaskLabel(task) : (TASK_TYPE_LABEL[task.task_type] ?? "账号组后台任务")} · {STATUS[task.status]}</div><div className="task-desc">{task.error_message || "未记录错误说明"}</div></div>
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

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <div><div className="panel-title">统一待办中心</div><div className="panel-sub">汇总各账号组的待核实任务、失败运营日志、素材审核、未读通知和 AI 配图失败。</div></div>
          <span className="tag">{todoCount} 项待处理</span>
        </div>
        <div className="filter-row" style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <select aria-label="待办来源筛选" value={todoSource} onChange={(event) => { setTodoSource(event.target.value as GlobalTodoItem["source"] | "all"); setTodoPage(1); }}>
            <option value="all">全部来源</option>
            <option value="global_queue">全局任务队列</option>
            <option value="task_run">任务追踪</option>
            <option value="ops_log">运营日志</option>
            <option value="circuit_breaker">平台熔断</option>
            <option value="hot_topic_source">热点采集失败</option>
            <option value="media_review">素材审核</option>
            <option value="notification">通知</option>
            <option value="media_generation">AI 配图</option>
          </select>
          <input aria-label="待办账号组筛选" value={todoTenantId} onChange={(event) => { setTodoTenantId(event.target.value); setTodoPage(1); }} placeholder="账号组 ID" style={{ width: 160 }} />
          <span className="tiny">队列 {todoCounts.global_queue ?? 0} · 任务 {todoCounts.task_run ?? 0} · 日志 {todoCounts.ops_log ?? 0} · 熔断 {todoCounts.circuit_breaker ?? 0} · 热点 {todoCounts.hot_topic_source ?? 0} · 素材 {todoCounts.media_review ?? 0} · 通知 {todoCounts.notification ?? 0} · 配图 {todoCounts.media_generation ?? 0}</span>
        </div>
        {todos.length ? <div className="list">
          {todos.map((todo) => <div className="task-row" key={todo.id}>
            <i className={`task-prio ${todo.severity === "high" ? "red" : "blue"}`} />
            <div className="task-main">
              <div className="task-title">{todo.title}</div>
              <div className="task-desc">{todo.tenant_name} · {todo.source_label} · {todo.detail} · {todo.created_at ? new Date(todo.created_at).toLocaleString("zh-CN") : "时间未知"}</div>
            </div>
            {todo.source === "notification" ? <button className="btn sm" disabled={loading} onClick={() => {
              void api.markNotificationRead(Number(todo.record_id)).then(() => refresh()).catch((e) => setError(e instanceof Error ? e.message : String(e)));
            }}>标记已读</button> : <button className="btn sm" onClick={() => navigate(todo.target_page, todo.target_page === "operations" ? (todo.target_tab ?? undefined) : undefined)}>处理</button>}
          </div>)}
        </div> : <div className="empty-state">当前筛选条件下没有待处理事项。</div>}
        {todoCapped && <div className="tiny" style={{ marginTop: 8 }}>部分来源记录较多，当前统计已提示截取范围；请缩小账号组或来源筛选。</div>}
        {todoPages > 1 && <div className="pagination-row" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, paddingTop: 12 }}>
          <button className="btn sm" disabled={todoPage <= 1 || loading} onClick={() => setTodoPage((p) => Math.max(1, p - 1))}>上一页</button>
          <span className="tiny">第 {todoPage} / {todoPages} 页 · 共 {todoCount} 项</span>
          <button className="btn sm" disabled={todoPage >= todoPages || loading} onClick={() => setTodoPage((p) => Math.min(todoPages, p + 1))}>下一页</button>
        </div>}
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-head">
          <div><div className="panel-title">未读通知</div><div className="panel-sub">通知已读状态会持久化；标记已读不会删除通知记录。</div></div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="tag">{unreadNotificationCount ?? "—"} 条未读</span>
            <button className="btn sm" disabled={!unreadNotificationCount || loading} onClick={() => {
              void api.markAllNotificationsRead().then(() => refresh()).catch((e) => setError(e instanceof Error ? e.message : String(e)));
            }}>全部标记已读</button>
          </div>
        </div>
        {unreadNotifications.length ? <div className="list">
          {unreadNotifications.map((notification) => <div className="task-row" key={notification.id}>
            <i className={`task-prio ${notification.level === "critical" || notification.level === "error" ? "red" : notification.level === "warning" ? "blue" : "green"}`} />
            <div className="task-main">
              <div className="task-title">{notification.title}</div>
              <div className="task-desc">{notification.body || notification.type} · {notification.platform ? (PLATFORMS[notification.platform] ?? notification.platform) : "系统"} · {new Date(notification.sent_at).toLocaleString("zh-CN")}</div>
            </div>
            <button className="btn sm primary" disabled={loading} onClick={() => {
              void api.markNotificationRead(notification.id)
                .then(() => {
                  void refresh();
                  navigate(
                    notification.target_page || "operations",
                    notification.target_tab || undefined,
                    notification.target_type && notification.target_id !== undefined && notification.target_id !== null
                      ? { type: notification.target_type, id: notification.target_id }
                      : null,
                  );
                })
                .catch((e) => setError(e instanceof Error ? e.message : String(e)));
            }}>查看并标记已读</button>
            <button className="btn sm" disabled={loading} onClick={() => {
              void api.markNotificationRead(notification.id).then(() => refresh()).catch((e) => setError(e instanceof Error ? e.message : String(e)));
            }}>仅标记已读</button>
          </div>)}
        </div> : <div className="empty-state">当前没有未读通知。</div>}
      </section>
    </div>
  );
}
