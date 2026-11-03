import { useEffect, useState } from "react";
import { api, GlobalTaskQueueSummary } from "../api";

const STATUS_LABEL: Record<string, string> = {
  queued: "等待执行", running: "执行中", succeeded: "已完成",
  failed: "执行失败", cancelled: "已取消", interrupted: "异常中断",
};
const STATUS_KEYS = ["queued", "running", "succeeded", "failed", "cancelled", "interrupted"] as const;

export default function TaskDispatchMetrics() {
  const [summary, setSummary] = useState<GlobalTaskQueueSummary | null>(null);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [taskType, setTaskType] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.taskQueueSummary({
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        tenantId: tenantId.trim() || undefined,
        taskType: taskType || undefined,
      });
      setSummary(result);
    } catch (err) {
      setError((err as Error).message || "任务统计加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const maxDaily = Math.max(1, ...(summary?.daily ?? []).map((item) => item.total));
  const statuses = summary?.by_status;
  return (
    <section className="panel" style={{ marginBottom: 18 }}>
      <div className="panel-head">
        <div>
          <h2>全局调度仪表盘</h2>
          <div className="muted">直接聚合共享任务队列；统计覆盖全部账号组，不依赖当前选中的账号组。</div>
        </div>
        <button className="btn sm" type="button" onClick={() => void refresh()} disabled={loading}>
          {loading ? "刷新中…" : "刷新统计"}
        </button>
      </div>
      <div className="filter-row" style={{ display: "flex", flexWrap: "wrap", gap: 8, padding: "0 16px 14px" }}>
        <label>开始日期 <input type="date" value={dateFrom} max={dateTo || undefined} onChange={(event) => setDateFrom(event.target.value)} /></label>
        <label>结束日期 <input type="date" value={dateTo} min={dateFrom || undefined} onChange={(event) => setDateTo(event.target.value)} /></label>
        <input aria-label="账号组 ID" value={tenantId} onChange={(event) => setTenantId(event.target.value)} placeholder="账号组 ID" style={{ width: 150 }} />
        <select aria-label="任务类型" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
          <option value="">全部任务类型</option>
          <option value="round_run">内容生产</option>
          <option value="scheduler_job">账号组后台任务</option>
        </select>
        <button className="btn sm soft" type="button" onClick={() => {
          setDateFrom(""); setDateTo(""); setTenantId(""); setTaskType("");
          setLoading(true);
          void api.taskQueueSummary().then(setSummary).then(() => setError("")).catch((err) => setError((err as Error).message || "任务统计加载失败")).finally(() => setLoading(false));
        }}>
          清空筛选
        </button>
        <button className="btn sm" type="button" onClick={() => void refresh()} disabled={loading}>应用筛选</button>
      </div>
      {error && <div className="notice danger" role="alert" style={{ margin: "0 16px 12px" }}>{error}</div>}
      {!summary && loading ? <div className="empty-state">正在汇总全局任务…</div> : !summary ? <div className="empty-state">统计数据暂不可用。</div> : (
        <>
          <div className="grid four" style={{ padding: "0 16px 16px" }}>
            <div className="metric"><div className="metric-label">筛选范围内任务</div><div className="metric-val">{summary.total}</div></div>
            <div className="metric"><div className="metric-label">运行中 / 等待</div><div className="metric-val">{summary.by_status.running} / {summary.by_status.queued}</div></div>
            <div className="metric"><div className="metric-label">成功 / 失败</div><div className="metric-val">{summary.by_status.succeeded} / {summary.by_status.failed}</div></div>
            <div className="metric"><div className="metric-label">平均执行时长</div><div className="metric-val">{summary.average_duration_seconds == null ? "—" : `${Math.round(summary.average_duration_seconds)} 秒`}</div></div>
          </div>
          <div className="grid two" style={{ padding: "0 16px 16px", gap: 16 }}>
            <div>
              <h3 style={{ margin: "0 0 10px" }}>任务状态分布</h3>
              {STATUS_KEYS.map((key) => {
                const count = statuses?.[key] ?? 0;
                const width = summary.total ? (count / summary.total) * 100 : 0;
                return <div key={key} style={{ display: "grid", gridTemplateColumns: "90px 1fr 48px", gap: 8, alignItems: "center", marginBottom: 8 }}>
                  <span className="tiny">{STATUS_LABEL[key]}</span>
                  <div style={{ height: 8, borderRadius: 8, background: "var(--line)", overflow: "hidden" }}>
                    <div style={{ width: `${width}%`, height: "100%", background: key === "failed" || key === "interrupted" ? "var(--red)" : key === "running" ? "var(--green)" : "var(--accent)" }} />
                  </div>
                  <strong className="tiny" style={{ textAlign: "right" }}>{count}</strong>
                </div>;
              })}
            </div>
            <div>
              <h3 style={{ margin: "0 0 10px" }}>每日任务趋势</h3>
              {summary.daily.length ? <div style={{ display: "flex", alignItems: "end", gap: 5, minHeight: 116, overflowX: "auto", paddingTop: 8 }}>
                {summary.daily.slice(-30).map((item) => (
                  <div key={item.date} title={`${item.date}：${item.total} 项，成功 ${item.succeeded}，失败 ${item.failed}`} style={{ minWidth: 18, flex: 1, display: "flex", flexDirection: "column", justifyContent: "end", alignItems: "center", gap: 4 }}>
                    <span className="tiny">{item.total}</span>
                    <div style={{ width: "100%", height: `${Math.max(3, (item.total / maxDaily) * 82)}px`, borderRadius: "4px 4px 0 0", background: item.failed ? "var(--red)" : "var(--accent)" }} />
                    <span className="tiny" style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}>{item.date.slice(5)}</span>
                  </div>
                ))}
              </div> : <div className="empty-state">当前筛选范围没有任务趋势数据。</div>}
            </div>
          </div>
          <div className="tiny" style={{ padding: "0 16px 14px" }}>
            统计时间字段：任务创建时间；平均执行时长仅统计有开始和结束时间的成功、失败及中断任务。
          </div>
        </>
      )}
    </section>
  );
}
