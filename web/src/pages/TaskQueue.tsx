import { useCallback, useEffect, useMemo, useState } from "react";
import { api, GlobalTaskQueueItem } from "../api";
import { useApp } from "../AppContext";

const STATUS_LABEL: Record<GlobalTaskQueueItem["status"], string> = {
  queued: "等待执行",
  running: "执行中",
  succeeded: "已完成",
  failed: "执行失败",
  cancelled: "已取消",
  interrupted: "中断待处理",
};

const TASK_LABEL: Record<string, string> = {
  round_run: "内容生产 / 存草稿",
  scheduler_job: "账号组后台任务",
};

const SCHEDULER_JOB_LABEL: Record<string, string> = {
  _job_hot_topics_refresh: "热点采集与匹配",
  _job_publish_polling: "发布状态轮询",
  _job_data_fetch: "平台数据采集",
  _job_daily_ops: "每日主动运营",
  _job_comment_polling: "评论轮询",
  _job_cb_recovery: "平台连接恢复",
  _job_kb_scan: "选题库扫描",
  _job_kb_sync: "选题库同步",
  _job_data_retention_cleanup: "数据清理",
  _job_round_timeout_sweeper: "轮次超时检查",
  _job_round_review: "轮次复盘",
  _job_autogrow_next_round: "自动闭环检查",
};

function aggregateLabel(task: GlobalTaskQueueItem) {
  if (!task.aggregate_id) return "—";
  if (task.task_type === "round_run") {
    const [, operation] = task.aggregate_id.split(":");
    return operation === "save_pending_drafts" ? "恢复存草稿任务" : "轮次生产任务";
  }
  if (task.task_type !== "scheduler_job") {
    return "关联业务任务";
  }
  const [method, objectId, attempt] = task.aggregate_id.split(":");
  const label = SCHEDULER_JOB_LABEL[method] || "账号组后台任务";
  if (method === "_job_data_fetch" && objectId) {
    return `${label}${attempt ? ` · 第 ${attempt} 次` : ""}`;
  }
  return label;
}

function timeLabel(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function TaskRow({
  task,
  onCancel,
  cancelling,
  onRetry,
  retrying,
  onResolve,
  resolving,
}: {
  task: GlobalTaskQueueItem;
  onCancel: (task: GlobalTaskQueueItem) => void;
  cancelling: boolean;
  onRetry: (task: GlobalTaskQueueItem) => void;
  retrying: boolean;
  onResolve: (task: GlobalTaskQueueItem, action: "handled" | "ignored") => void;
  resolving: boolean;
}) {
  const active = task.status === "queued" || task.status === "running";
  return (
    <div className="panel-row" style={{ display: "grid", gridTemplateColumns: "minmax(180px, 1.5fr) minmax(100px, .8fr) minmax(100px, .8fr) minmax(150px, 1fr) auto", gap: 14, alignItems: "center", padding: "14px 16px" }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 650 }}>{TASK_LABEL[task.task_type] || task.task_type}</div>
        <div className="muted" style={{ fontSize: 12, marginTop: 4, overflowWrap: "anywhere" }}>
          {task.tenant_name} · {aggregateLabel(task)}
        </div>
      </div>
      <div>
        <span className={`status-pill ${task.status === "failed" || task.status === "interrupted" ? "failed" : task.status === "running" ? "producing" : task.status === "succeeded" ? "completed" : task.status === "cancelled" ? "cancelled" : "creating"}`}>
          {STATUS_LABEL[task.status]}
        </span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>{timeLabel(task.created_at)}</div>
      <div className="muted" style={{ fontSize: 12 }}>
        {task.error_message || (
          task.status === "queued" && new Date(task.available_at).getTime() > Date.now()
            ? `计划执行：${timeLabel(task.available_at)}`
            : task.started_at ? `开始：${timeLabel(task.started_at)}` : "尚未开始"
        )}
        {task.finished_at && <div style={{ marginTop: 4 }}>结束：{timeLabel(task.finished_at)}</div>}
        {task.started_at && task.finished_at && <div>耗时：{Math.max(0, (new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000).toFixed(1)} 秒</div>}
      </div>
      <div>
        {task.status === "queued" && (
          <button className="btn sm soft" type="button" disabled={cancelling} onClick={() => onCancel(task)}>
            {cancelling ? "取消中…" : "取消"}
          </button>
        )}
        {(task.status === "failed" || task.status === "interrupted") && (
          task.resolution_action ? (
            <div className="muted" style={{ fontSize: 12, maxWidth: 260 }}>
              <div>
                {task.resolution_action === "handled" ? "已处理" : "已忽略"}
                {task.resolution_actor ? " · " + task.resolution_actor : ""}
              </div>
              {task.resolution_at && <div style={{ marginTop: 3 }}>{timeLabel(task.resolution_at)}</div>}
              {task.resolution_note && <div style={{ marginTop: 3, overflowWrap: "anywhere" }}>{task.resolution_note}</div>}
            </div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <button className="btn sm soft" type="button" disabled={resolving} onClick={() => onResolve(task, "handled")}>
                {resolving ? "提交中…" : "已处理"}
              </button>
              <button className="btn sm ghost" type="button" disabled={resolving} onClick={() => onResolve(task, "ignored")}>
                忽略
              </button>
              <button className="btn sm soft" type="button" disabled={retrying || resolving} onClick={() => onRetry(task)}>
                {retrying ? "恢复中…" : "人工核验后恢复"}
              </button>
            </div>
          )
        )}
        {active && task.status === "running" && <span className="muted" style={{ fontSize: 12 }}>按队列顺序执行</span>}
      </div>
    </div>
  );
}

export default function TaskQueue() {
  const { showToast } = useApp();
  const [activeItems, setActiveItems] = useState<GlobalTaskQueueItem[]>([]);
  const [activeTotal, setActiveTotal] = useState(0);
  const [activePages, setActivePages] = useState(1);
  const [activePage, setActivePage] = useState(1);
  const [activeCounts, setActiveCounts] = useState({ queued: 0, running: 0 });
  const [historyItems, setHistoryItems] = useState<GlobalTaskQueueItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyPages, setHistoryPages] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyType, setHistoryType] = useState<"all" | "round_run" | "scheduler_job">("round_run");
  const [historyStatus, setHistoryStatus] = useState<"all" | "failed">("all");
  const [historyTenantId, setHistoryTenantId] = useState("");
  const [historyErrorKeyword, setHistoryErrorKeyword] = useState("");
  const [historyDateFrom, setHistoryDateFrom] = useState("");
  const [historyDateTo, setHistoryDateTo] = useState("");
  const activePageSize = 20;
  const historyPageSize = 20;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [retryTask, setRetryTask] = useState<GlobalTaskQueueItem | null>(null);
  const [retryNote, setRetryNote] = useState("");
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [resolutionTask, setResolutionTask] = useState<GlobalTaskQueueItem | null>(null);
  const [resolutionAction, setResolutionAction] = useState<"handled" | "ignored">("handled");
  const [resolutionNote, setResolutionNote] = useState("");
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [activeResponse, historyResponse] = await Promise.all([
        api.taskQueue({ scope: "active", page: activePage, pageSize: activePageSize }),
        api.taskQueue({
          scope: historyStatus === "failed" ? "failed" : "history",
          page: historyPage, pageSize: historyPageSize,
          taskType: historyType === "all" ? undefined : historyType,
          tenantId: historyTenantId.trim() || undefined,
          errorKeyword: historyErrorKeyword.trim() || undefined,
          dateFrom: historyDateFrom || undefined,
          dateTo: historyDateTo || undefined,
        }),
      ]);
      setActiveItems(activeResponse.items || []);
      setActiveTotal(activeResponse.total ?? 0);
      const nextActivePages = activeResponse.pages ?? 1;
      setActivePages(nextActivePages);
      if (activePage > nextActivePages) setActivePage(nextActivePages);
      setActiveCounts({
        queued: activeResponse.status_counts?.queued ?? 0,
        running: activeResponse.status_counts?.running ?? 0,
      });
      setHistoryItems(historyResponse.items || []);
      setHistoryTotal(historyResponse.total ?? 0);
      const nextHistoryPages = historyResponse.pages ?? 1;
      setHistoryPages(nextHistoryPages);
      if (historyPage > nextHistoryPages) setHistoryPage(nextHistoryPages);
      setError("");
    } catch (err) {
      setError((err as Error).message || "任务队列读取失败");
    } finally {
      setLoading(false);
    }
  }, [activePage, historyPage, historyType, historyStatus, historyTenantId, historyErrorKeyword, historyDateFrom, historyDateTo]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const active = useMemo(
    () => [...activeItems].sort((a, b) => {
      const aDelayed = a.status === "queued" && new Date(a.available_at).getTime() > Date.now();
      const bDelayed = b.status === "queued" && new Date(b.available_at).getTime() > Date.now();
      if (aDelayed !== bDelayed) return aDelayed ? 1 : -1;
      return a.queue_order - b.queue_order;
    }),
    [activeItems],
  );
  const history = historyItems;

  const cancel = async (task: GlobalTaskQueueItem) => {
    setCancellingId(task.task_id);
    try {
      await api.cancelTask(task.task_id);
      showToast("排队任务已取消", "success");
      await refresh();
    } catch (err) {
      showToast((err as Error).message || "取消失败", "error");
    } finally {
      setCancellingId(null);
    }
  };

  const requestResolve = (task: GlobalTaskQueueItem, action: "handled" | "ignored") => {
    setResolutionTask(task);
    setResolutionAction(action);
    setResolutionNote("");
  };

  const resolveTask = async () => {
    if (!resolutionTask || resolutionNote.trim().length < 8) return;
    setResolvingId(resolutionTask.task_id);
    try {
      const result = await api.resolveTask(resolutionTask.task_id, {
        action: resolutionAction,
        note: resolutionNote.trim(),
      });
      showToast(result.created ? (resolutionAction === "handled" ? "任务已标记处理完成" : "任务已忽略") : "该任务此前已处理", "success");
      setResolutionTask(null);
      setResolutionNote("");
      await refresh();
    } catch (err) {
      showToast((err as Error).message || "任务处理失败", "error");
    } finally {
      setResolvingId(null);
    }
  };

  const requestRetry = (task: GlobalTaskQueueItem) => {
    setRetryTask(task);
    setRetryNote("");
    setRetryConfirmed(false);
  };

  const retry = async () => {
    if (!retryTask || !retryConfirmed || retryNote.trim().length < 8) return;
    setRetryingId(retryTask.task_id);
    try {
      const result = await api.retryTask(retryTask.task_id, {
        confirmedNotExecuted: true,
        confirmationNote: retryNote.trim(),
      });
      showToast(
        result.created ? "已创建人工核验恢复任务" : "该任务已有恢复子任务，未重复创建",
        "success",
      );
      setRetryTask(null);
      setRetryNote("");
      setRetryConfirmed(false);
      await refresh();
    } catch (err) {
      showToast((err as Error).message || "恢复任务创建失败", "error");
    } finally {
      setRetryingId(null);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">GLOBAL EXECUTION</div>
          <h1>全局任务队列</h1>
          <p className="muted">跨账号组统一排队。切换当前账号组不会暂停、取消或改变已提交任务的执行顺序。</p>
        </div>
        <button className="btn sm soft" type="button" onClick={() => void refresh()}>立即刷新</button>
      </div>

      <div className="queue-summary-grid" style={{ marginBottom: 18 }}>
        <div className="panel stat-card"><div className="muted">排队与执行</div><div className="stat-value">{activeCounts.queued + activeCounts.running}</div></div>
        <div className="panel stat-card"><div className="muted">正在执行</div><div className="stat-value">{activeCounts.running}</div></div>
        <div className="panel stat-card"><div className="muted">等待执行</div><div className="stat-value">{activeCounts.queued}</div></div>
      </div>

      {error && <div className="notice danger" role="alert">{error}</div>}
      <section className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-head">
          <div><h2>待执行任务</h2><div className="muted">已到执行时间的任务按入队顺序执行；延迟任务到期后参与排序，同一时刻只执行一个队列任务。</div></div>
          <span className="muted">共 {activeTotal} 项 · 第 {activePage}/{activePages} 页</span>
        </div>
        {loading ? <div className="empty-state">正在读取队列…</div> : active.length ? (
          <div>{active.map((task) => <TaskRow key={task.task_id} task={task} onCancel={cancel} cancelling={cancellingId === task.task_id} onRetry={requestRetry} retrying={retryingId === task.task_id} onResolve={requestResolve} resolving={resolvingId === task.task_id} />)}</div>
        ) : <div className="empty-state">当前没有等待或执行中的任务。</div>}
        <div className="panel-foot" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "12px 16px" }}>
          <span className="muted">每页 {activePageSize} 条，队列列表由服务端分页读取。</span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="btn sm" disabled={activePage <= 1 || loading} onClick={() => setActivePage((p) => Math.max(1, p - 1))}>上一页</button>
            <span className="muted">{activePage} / {activePages}</span>
            <button className="btn sm" disabled={activePage >= activePages || loading} onClick={() => setActivePage((p) => Math.min(activePages, p + 1))}>下一页</button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div><h2>最近任务</h2><div className="muted">支持按任务类型、失败状态、账号组 ID、失败原因和创建日期筛选；系统后台调度任务可单独查看。</div></div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label="历史任务类型" value={historyType} onChange={(event) => { setHistoryType(event.target.value as "all" | "round_run" | "scheduler_job"); setHistoryPage(1); }}>
              <option value="round_run">内容生产</option><option value="scheduler_job">系统后台任务</option><option value="all">全部任务类型</option>
            </select>
            <select aria-label="历史任务状态" value={historyStatus} onChange={(event) => { setHistoryStatus(event.target.value as "all" | "failed"); setHistoryPage(1); }}>
              <option value="all">全部状态</option><option value="failed">失败 / 中断待处理</option>
            </select>
            <input aria-label="按账号组 ID 筛选" value={historyTenantId} onChange={(event) => { setHistoryTenantId(event.target.value); setHistoryPage(1); }} placeholder="账号组 ID" style={{ width: 130 }} />
            <input aria-label="按失败原因筛选" value={historyErrorKeyword} onChange={(event) => { setHistoryErrorKeyword(event.target.value); setHistoryPage(1); }} placeholder="失败原因关键字" style={{ width: 150 }} />
            <input aria-label="历史任务开始日期" type="date" value={historyDateFrom} onChange={(event) => { setHistoryDateFrom(event.target.value); setHistoryPage(1); }} />
            <input aria-label="历史任务结束日期" type="date" value={historyDateTo} onChange={(event) => { setHistoryDateTo(event.target.value); setHistoryPage(1); }} />
            <button className="btn sm ghost" onClick={() => { setHistoryType("all"); setHistoryStatus("all"); setHistoryTenantId(""); setHistoryErrorKeyword(""); setHistoryDateFrom(""); setHistoryDateTo(""); setHistoryPage(1); }}>清空筛选</button>
            <span className="muted">共 {historyTotal} 项 · 第 {historyPage}/{historyPages} 页</span>
          </div>
        </div>
        {history.length ? <div>{history.map((task) => <TaskRow key={task.task_id} task={task} onCancel={cancel} cancelling={false} onRetry={requestRetry} retrying={retryingId === task.task_id} onResolve={requestResolve} resolving={resolvingId === task.task_id} />)}</div> : <div className="empty-state">暂无历史任务。</div>}
        <div className="panel-foot" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "12px 16px" }}>
          <span className="muted">每页 {historyPageSize} 条，历史记录与失败任务均由服务端分页读取。</span>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button className="btn sm" disabled={historyPage <= 1 || loading} onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}>上一页</button>
            <span className="muted">{historyPage} / {historyPages}</span>
            <button className="btn sm" disabled={historyPage >= historyPages || loading} onClick={() => setHistoryPage((p) => Math.min(historyPages, p + 1))}>下一页</button>
          </div>
        </div>
      </section>

      {retryTask && (
        <div
          role="presentation"
          onClick={(event) => { if (event.target === event.currentTarget && !retryingId) setRetryTask(null); }}
          style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(15, 23, 42, .48)", display: "grid", placeItems: "center", padding: 20 }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-retry-title"
            className="panel"
            style={{ width: "min(560px, 100%)", padding: 22, boxShadow: "0 20px 60px rgba(0,0,0,.24)" }}
          >
            <h2 id="task-retry-title">人工核验后恢复任务</h2>
            <p className="muted" style={{ marginTop: 8 }}>
              原任务：{retryTask.task_id} · 账号组：{retryTask.tenant_name}
            </p>
            <div role="alert" style={{ margin: "14px 0", padding: 12, borderRadius: 10, border: "1px solid var(--red)", background: "var(--red-weak)", color: "var(--text)", fontSize: 13, lineHeight: 1.6 }}>
              仅当你已在对应平台核实“该操作没有执行、没有创建草稿或发布记录”时才能恢复。
              如果平台状态不确定，请取消并先人工核查。系统不会自动重试。
            </div>
            <label style={{ display: "block", fontWeight: 600, marginBottom: 6 }} htmlFor="task-retry-note">
              平台核验说明（至少 8 个字符）
            </label>
            <textarea
              id="task-retry-note"
              value={retryNote}
              onChange={(event) => setRetryNote(event.target.value)}
              rows={4}
              maxLength={500}
              placeholder="例如：已在小红书创作者后台按内容标题和账号核查，没有对应草稿或发布记录。"
              style={{ width: "100%", resize: "vertical" }}
            />
            <label style={{ display: "flex", alignItems: "flex-start", gap: 8, marginTop: 14 }}>
              <input
                type="checkbox"
                checked={retryConfirmed}
                onChange={(event) => setRetryConfirmed(event.target.checked)}
              />
              <span>我已核实平台侧没有执行结果，确认允许创建新的恢复任务。</span>
            </label>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
              <button className="btn sm" type="button" disabled={!!retryingId} onClick={() => setRetryTask(null)}>取消</button>
              <button
                className="btn sm soft"
                type="button"
                disabled={!retryConfirmed || retryNote.trim().length < 8 || !!retryingId}
                onClick={() => void retry()}
              >
                {retryingId ? "正在创建…" : "确认创建恢复任务"}
              </button>
            </div>
          </section>
        </div>
      )}

      {resolutionTask && (
        <div
          role="presentation"
          onClick={(event) => { if (event.target === event.currentTarget && !resolvingId) setResolutionTask(null); }}
          style={{ position: "fixed", inset: 0, zIndex: 1001, background: "rgba(15, 23, 42, .48)", display: "grid", placeItems: "center", padding: 20 }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-resolution-title"
            className="panel"
            style={{ width: "min(560px, 100%)", padding: 22, boxShadow: "0 20px 60px rgba(0,0,0,.24)" }}
          >
            <h2 id="task-resolution-title">{resolutionAction === "handled" ? "标记任务已处理" : "忽略此任务"}</h2>
            <p className="muted" style={{ marginTop: 8 }}>
              原任务：{resolutionTask.task_id} · 账号组：{resolutionTask.tenant_name}
            </p>
            <div role="alert" className="notice" style={{ margin: "14px 0" }}>
              此操作只关闭待办，不会修改原始失败状态，也不会重新执行任务。处理记录会永久保留。
            </div>
            <label style={{ display: "block", fontWeight: 600, marginBottom: 6 }} htmlFor="task-resolution-note">
              {resolutionAction === "handled" ? "处理说明" : "忽略原因"}（至少 8 个字符）
            </label>
            <textarea
              id="task-resolution-note"
              value={resolutionNote}
              onChange={(event) => setResolutionNote(event.target.value)}
              rows={4}
              maxLength={500}
              placeholder={resolutionAction === "handled" ? "说明已核查的问题及采取的处理措施。" : "说明为什么该异常无需继续跟进。"}
              style={{ width: "100%", resize: "vertical" }}
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
              <button className="btn sm" type="button" disabled={!!resolvingId} onClick={() => setResolutionTask(null)}>取消</button>
              <button
                className="btn sm soft"
                type="button"
                disabled={resolutionNote.trim().length < 8 || !!resolvingId}
                onClick={() => void resolveTask()}
              >
                {resolvingId ? "正在保存…" : "确认"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
