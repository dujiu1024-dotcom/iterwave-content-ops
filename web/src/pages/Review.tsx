import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ReviewQueueItem } from "../api";
import { ALL_PLATFORMS, platformMeta, Platform } from "../platforms";

const PAGE_SIZE = 30;

function actionLabel(action: string): string {
  if (action === "review_approve") return "审核通过";
  if (action === "review_reject") return "审核驳回";
  if (action === "review_resubmit") return "重新提交";
  return action;
}

function statusLabel(status: string): string {
  if (status === "approved") return "已通过";
  if (status === "rejected") return "已驳回";
  return "待审核";
}

export default function Review() {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("pending");
  const [platform, setPlatform] = useState<"all" | Platform>("all");
  const [taskType, setTaskType] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [resubmitReason, setResubmitReason] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.reviewQueue({
        status, platform, taskType: taskType || undefined,
        from: from || undefined, to: to || undefined,
        page, pageSize: PAGE_SIZE,
      });
      setItems(result.items);
      setTotal(result.total);
      setSelectedId((current) => current && result.items.some((item) => item.id === current)
        ? current : result.items[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "审核队列加载失败");
    } finally {
      setLoading(false);
    }
  }, [status, platform, taskType, from, to, page]);

  useEffect(() => { void load(); }, [load]);

  const selected = items.find((item) => item.id === selectedId) ?? null;
  const filteredItems = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return items;
    return items.filter((item) =>
      [item.title, item.id, item.topic_id ?? "", item.round_number ?? ""]
        .some((value) => String(value).toLowerCase().includes(query)),
    );
  }, [items, search]);

  const runAction = async (item: ReviewQueueItem, action: "approve" | "reject" | "resubmit") => {
    if (busyId) return;
    if (action === "reject" && !rejectReason.trim()) {
      setNotice("请填写驳回理由。");
      return;
    }
    if (action === "resubmit" && !resubmitReason.trim()) {
      setNotice("请填写重新提交审核的说明。");
      return;
    }
    setBusyId(item.id);
    setNotice("");
    setError("");
    try {
      if (action === "approve") {
        await api.approveContent(item.id, "集中审核工作区人工审核通过");
        setNotice("审核通过已记录；未触发发布。");
      } else if (action === "reject") {
        await api.rejectContent(item.id, rejectReason.trim());
        setRejectReason("");
        setNotice("已驳回，并记录处理原因。");
      } else {
        await api.resubmitContent(item.id, resubmitReason.trim());
        setResubmitReason("");
        setNotice("已重新提交审核，等待再次处理。");
      }
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : "审核操作失败";
      // 409 通常意味着状态已被其他审核员改变，刷新后以服务端状态为准。
      await load();
      setError(message);
    } finally {
      setBusyId(null);
    }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="review-page">
      <div className="review-header">
        <div>
          <div className="review-eyebrow">CONTENT OPERATIONS</div>
          <h1>集中审核</h1>
          <p className="muted">跨生产轮次统一处理内容审核。审核通过不会自动发布，发布仍需单独授权。</p>
        </div>
        <button className="btn" type="button" onClick={() => void load()} disabled={loading}>刷新队列</button>
      </div>

      <div className="review-stats">
        <div className="review-stat"><span>当前筛选结果</span><strong>{total}</strong></div>
        <div className="stat-card"><span>当前页</span><strong>{page} / {pages}</strong></div>
        <div className="stat-card"><span>每页数量</span><strong>{PAGE_SIZE}</strong></div>
      </div>

      <section className="panel" style={{ marginBottom: 16 }}>
        <div className="review-panel-head"><div className="panel-title">筛选条件</div></div>
        <div className="review-filters">
          <label>审核状态
            <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
              <option value="pending">待审核</option><option value="approved">已通过</option>
              <option value="rejected">已驳回</option><option value="all">全部</option>
            </select>
          </label>
          <label>平台
            <select value={platform} onChange={(e) => { setPage(1); setPlatform(e.target.value as "all" | Platform); }}>
              <option value="all">全部平台</option>
              {ALL_PLATFORMS.map((p) => <option key={p} value={p}>{platformMeta(p).label}</option>)}
            </select>
          </label>
          <label>任务类型 / 草稿状态
            <select value={taskType} onChange={(e) => { setPage(1); setTaskType(e.target.value); }}>
              <option value="">全部类型</option><option value="draft_pending">本地待存草稿</option>
              <option value="draft_saved">已存平台草稿</option>
            </select>
          </label>
          <label>开始日期<input type="date" value={from} max={to || undefined} onChange={(e) => { setPage(1); setFrom(e.target.value); }} /></label>
          <label>结束日期<input type="date" value={to} min={from || undefined} onChange={(e) => { setPage(1); setTo(e.target.value); }} /></label>
          <label>关键词<input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="标题 / 内容 ID / 轮次" /></label>
        </div>
      </section>

      {notice && <div className="review-notice success" role="status">{notice}</div>}
      {error && <div className="review-notice error" role="alert">{error}</div>}

      <div className="review-workspace" style={{ display: "grid", gridTemplateColumns: "minmax(280px, 0.9fr) minmax(0, 1.5fr)", gap: 16, alignItems: "start" }}>
        <section className="panel" aria-label="审核内容列表">
          <div className="panel-header"><div className="panel-title">审核队列（{filteredItems.length} / {total}）</div></div>
          {loading ? <div className="review-empty">正在加载审核队列…</div>
            : filteredItems.length === 0 ? <div className="empty-state">当前筛选条件下没有内容。</div>
            : <div className="review-queue-list">
              {filteredItems.map((item) => (
                <button key={item.id} type="button"
                  className={`review-queue-item ${selectedId === item.id ? "active" : ""}`}
                  onClick={() => { setSelectedId(item.id); setRejectReason(""); setResubmitReason(""); }}>
                  <span className="review-queue-item-top">
                    <strong>{item.title || "未命名内容"}</strong>
                    <span className={`status-pill status-${item.review_status}`}>{statusLabel(item.review_status)}</span>
                  </span>
                  <span className="muted">{platformMeta(item.platform).label} · 第 {item.round_number ?? "—"} 轮 · {item.draft_status}</span>
                  <span className="muted">{item.created_at || "时间未知"}</span>
                </button>
              ))}
            </div>}
          <div className="review-pager">
            <button className="btn sm" disabled={page <= 1 || loading} onClick={() => setPage((n) => n - 1)}>上一页</button>
            <span className="muted">{page} / {pages}</span>
            <button className="btn sm" disabled={page >= pages || loading} onClick={() => setPage((n) => n + 1)}>下一页</button>
          </div>
        </section>

        <section className="panel" aria-label="审核详情">
          <div className="panel-header"><div className="panel-title">审核详情</div></div>
          {!selected ? <div className="empty-state">选择左侧内容查看详情和处理历史。</div> : <>
            <div className="review-detail">
              <h2>{selected.title || "未命名内容"}</h2>
              <div className="muted">内容 ID：{selected.id}</div>
              <div className="review-detail-meta">
                <span>{platformMeta(selected.platform).label}</span>
                <span>生产轮次：{selected.round_number ?? "—"}</span>
                <span>轮次状态：{selected.round_status ?? "—"}</span>
                <span>草稿状态：{selected.draft_status}</span>
                <span>G2：{selected.guard_outcome ?? "未检查"}</span>
                <span>审核：{statusLabel(selected.review_status)}</span>
              </div>
              <section className="review-content-preview" aria-label="待审核正文">
                <h3>内容正文</h3>
                <div className="review-body">{selected.body || "（正文为空）"}</div>
                {selected.tags.length > 0 && <div className="review-tags">{selected.tags.map((tag) => <span key={tag}>#{tag}</span>)}</div>}
                {selected.image_count > 0 && <div className="review-image-grid">
                  {Array.from({ length: Math.min(selected.image_count, 4) }, (_, index) => (
                    <a key={index} href={api.contentImageUrl(selected.id, index)} target="_blank" rel="noreferrer">
                      <img src={api.contentImageUrl(selected.id, index)} alt={`内容图片 ${index + 1}`} loading="lazy" />
                    </a>
                  ))}
                  {selected.image_count > 4 && <span className="muted">另有 {selected.image_count - 4} 张图片</span>}
                </div>}
              </section>
              {selected.guard_reason && <p className="muted">质量检查说明：{selected.guard_reason}</p>}
              {selected.review_rejection_reason && <p className="notice error">驳回原因：{selected.review_rejection_reason}</p>}
              {selected.draft_error && <p className="muted">草稿说明：{selected.draft_error}</p>}
              <div className="review-actions">
                {selected.review_status === "rejected" ? <>
                  <p className="muted">该内容已驳回。必须先通过 G2 质量检查，再明确重新提交审核；不能直接改为通过。</p>
                  <div className="review-reject-form">
                    <textarea value={resubmitReason} onChange={(e) => setResubmitReason(e.target.value)}
                      placeholder="填写修改说明 / 重新提交理由（必填）" rows={3} maxLength={500} />
                    <button className="btn primary" type="button"
                      disabled={busyId !== null || !resubmitReason.trim() || selected.guard_outcome !== "pass"}
                      onClick={() => void runAction(selected, "resubmit")}>重新提交审核</button>
                  </div>
                </> : <>
                  <button className="btn primary" type="button"
                    disabled={busyId !== null || selected.review_status === "approved" || selected.guard_outcome !== "pass"}
                    onClick={() => void runAction(selected, "approve")}>
                    {busyId === selected.id ? "处理中…" : "审核通过"}
                  </button>
                  <div className="review-reject-form">
                    <textarea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
                      placeholder="填写驳回理由（必填）" rows={3} maxLength={500} />
                    <button className="btn danger" type="button"
                      disabled={busyId !== null || !rejectReason.trim()}
                      onClick={() => void runAction(selected, "reject")}>驳回内容</button>
                  </div>
                </>}
              </div>
            </div>
            <div className="review-history">
              <h3>审核历史</h3>
              {selected.review_history.length === 0 ? <p className="muted">暂无可关联到该内容的审核操作记录。</p>
                : <ol>{selected.review_history.map((event, index) => (
                  <li key={`${event.overridden_at}-${index}`}>
                    <strong>{actionLabel(event.override_field)}</strong>
                    <span>{event.reason || event.override_value}</span>
                    <small>{event.overridden_at} · {event.overridden_by}</small>
                  </li>
                ))}</ol>}
            </div>
          </>}
        </section>
      </div>
      <style>{`
        .review-page { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
        .review-header { display: flex; justify-content: space-between; align-items: start; gap: 16px; }
        .review-header h1 { margin: 4px 0; font-size: 24px; }
        .review-header p { margin: 6px 0 0; max-width: 760px; }
        .review-eyebrow { color: var(--muted); font-size: 10px; letter-spacing: .12em; font-weight: 700; }
        .review-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
        .review-stat { border: 1px solid var(--line); background: var(--surface); border-radius: 10px; padding: 12px 14px; display: grid; gap: 5px; }
        .review-stat span { color: var(--muted); font-size: 11px; } .review-stat strong { font-size: 20px; }
        .review-panel-head { padding: 14px 16px; border-bottom: 1px solid var(--line); font-weight: 700; }
        .review-filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; padding: 14px 16px; }
        .review-filters label { display: grid; gap: 5px; color: var(--muted); font-size: 11px; min-width: 140px; flex: 1 1 140px; }
        .review-filters input, .review-filters select { width: 100%; min-width: 0; }
        .review-notice { padding: 10px 13px; border-radius: 8px; font-size: 12px; }
        .review-notice.success { background: var(--green-weak); color: var(--green); }
        .review-notice.error { background: var(--red-weak); color: var(--red); }
        .review-empty { padding: 28px 16px; text-align: center; color: var(--muted); font-size: 12px; }
        .review-pager { display: flex; justify-content: space-between; align-items: center; padding: 12px; }
        @media (max-width: 900px) { .review-workspace { grid-template-columns: minmax(0, 1fr) !important; } }
        @media (max-width: 560px) {
          .review-header { flex-direction: column; }
          .review-stats { grid-template-columns: 1fr; }
          .review-detail { padding: 12px; }
        }
        .review-queue-list { display: grid; gap: 1px; }
        .review-queue-item { display: grid; gap: 7px; width: 100%; text-align: left; padding: 13px; border: 0; border-bottom: 1px solid var(--border); background: transparent; color: inherit; cursor: pointer; }
        .review-queue-item.active { background: var(--surface-2); outline: 1px solid var(--accent); }
        .review-queue-item-top { display: flex; justify-content: space-between; gap: 10px; align-items: start; }
        .review-queue-item-top strong { overflow-wrap: anywhere; }
        .status-pill { white-space: nowrap; font-size: 12px; padding: 2px 7px; border-radius: 99px; background: var(--surface-2); }
        .status-approved { color: var(--green); } .status-rejected { color: var(--danger); }
        .review-detail { padding: 16px; } .review-detail h2 { margin-top: 0; overflow-wrap: anywhere; }
        .review-content-preview { margin-top: 16px; padding: 14px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-2); }
        .review-content-preview h3 { margin: 0 0 9px; font-size: 13px; }
        .review-body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; font-size: 13px; max-height: 420px; overflow: auto; }
        .review-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; color: var(--muted); font-size: 12px; }
        .review-image-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
        .review-image-grid img { width: 88px; height: 110px; object-fit: cover; border-radius: 7px; border: 1px solid var(--line); }
        .review-detail-meta { display: flex; flex-wrap: wrap; gap: 8px 14px; margin: 14px 0; font-size: 13px; }
        .review-actions { display: grid; gap: 14px; margin-top: 20px; }
        .review-reject-form { display: grid; gap: 8px; }
        .review-reject-form textarea { width: 100%; resize: vertical; }
        .review-history { border-top: 1px solid var(--border); padding: 16px; }
        .review-history ol { padding-left: 20px; display: grid; gap: 12px; }
        .review-history li { display: grid; gap: 4px; }
        .review-history small { color: var(--muted); }
      `}</style>
    </div>
  );
}
