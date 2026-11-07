import { useCallback, useEffect, useMemo, useState } from "react";
import StructuredDataView from "./StructuredDataView";
import {
  api, BusinessGapItem, Draft, MetricFactItem, PublishVerificationItem, ReviewTaskItem,
} from "../api";
import { displayPlatformLabel } from "../displayLabels";

type VerificationForm = { platform_content_id: string; content_url: string; published_at: string; evidence_note: string };
type CollectionForm = {
  views: string; likes: string; collects: string; comments: string; shares: string;
  status: "succeeded" | "partial"; error: string;
};
type ConclusionForm = {
  conclusion: string; evidence_note: string; target_impact: string; recommendations: string;
  gap_key: string; gap_title: string; gap_description: string;
  severity: "low" | "medium" | "high" | "critical";
  gap_status: "active" | "resolved"; gap_reason: string;
};

const METRIC_FIELDS = ["views", "likes", "collects", "comments", "shares"] as const;
const metricLabels: Record<(typeof METRIC_FIELDS)[number], string> = {
  views: "曝光", likes: "点赞", collects: "收藏", comments: "评论", shares: "分享",
};
const emptyCollection: CollectionForm = {
  views: "", likes: "", collects: "", comments: "", shares: "", status: "partial", error: "",
};
const emptyConclusion: ConclusionForm = {
  conclusion: "", evidence_note: "", target_impact: "", recommendations: "",
  gap_key: "", gap_title: "", gap_description: "",
  severity: "medium", gap_status: "active", gap_reason: "",
};

function localDateTime(value?: string | null): string {
  if (!value) return new Date().toISOString().slice(0, 16);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return new Date().toISOString().slice(0, 16);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function statusText(value: string): string {
  const labels: Record<string, string> = {
    pending: "待处理", scheduled: "待到期", processing: "处理中", succeeded: "成功",
    partial: "部分成功", failed: "失败", completed: "已完成", cancelled: "已取消",
    delivered: "已关闭", active: "待解决", resolved: "已解决", not_required: "无需关闭",
  };
  return labels[value] ?? value;
}
function displayMetric(value: number, unit: string): string {
  return unit === "ratio" ? `${(value * 100).toFixed(2)}%` : value.toLocaleString();
}

export default function OperatingFeedbackPane() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [verifications, setVerifications] = useState<PublishVerificationItem[]>([]);
  const [tasks, setTasks] = useState<ReviewTaskItem[]>([]);
  const [gaps, setGaps] = useState<BusinessGapItem[]>([]);
  const [facts, setFacts] = useState<MetricFactItem[]>([]);
  const [verificationForms, setVerificationForms] = useState<Record<string, VerificationForm>>({});
  const [collectionForms, setCollectionForms] = useState<Record<string, CollectionForm>>({});
  const [conclusionForms, setConclusionForms] = useState<Record<string, ConclusionForm>>({});
  const [history, setHistory] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [runHistory, setRunHistory] = useState<Record<string, Array<Record<string, unknown>>>>({});
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [allDrafts, verificationResult, taskResult, gapResult, factResult] = await Promise.all([
        api.draftsAll(), api.publishVerifications(), api.reviewTasks(),
        api.businessGaps("active"), api.metricFacts(undefined, undefined, 30),
      ]);
      setDrafts(allDrafts);
      setVerifications(verificationResult.items);
      setTasks(taskResult.items);
      setGaps(gapResult.items);
      setFacts(factResult.items);
      setVerificationForms((current) => {
        const next = { ...current };
        for (const draft of allDrafts) {
          if (!next[draft.id]) next[draft.id] = {
            platform_content_id: "", content_url: draft.platform_note_url || "",
            published_at: localDateTime(draft.published_at || draft.user_confirmed_at),
            evidence_note: "",
          };
        }
        return next;
      });
      setCollectionForms((current) => {
        const next = { ...current };
        for (const task of taskResult.items) if (!next[task.task_id]) next[task.task_id] = { ...emptyCollection };
        return next;
      });
      setConclusionForms((current) => {
        const next = { ...current };
        for (const task of taskResult.items) if (!next[task.task_id]) next[task.task_id] = { ...emptyConclusion };
        return next;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "经营复盘数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const verifiedDraftIds = useMemo(() => new Set(verifications.map((item) => item.draft_id)), [verifications]);
  const needsVerification = useMemo(
    () => drafts.filter((draft) => !!draft.user_confirmed_at && !verifiedDraftIds.has(draft.id)),
    [drafts, verifiedDraftIds],
  );

  const updateVerification = (draftId: string, key: keyof VerificationForm, value: string) => {
    setVerificationForms((current) => ({
      ...current, [draftId]: { ...(current[draftId] ?? {
        platform_content_id: "", content_url: "", published_at: localDateTime(), evidence_note: "",
      }), [key]: value },
    }));
  };

  const submitVerification = async (draft: Draft) => {
    const form = verificationForms[draft.id];
    if (!form?.platform_content_id.trim() || !form.content_url.trim() || !form.published_at) {
      setError("发布核验必须填写平台内容 ID、内容链接和真实发布时间。");
      return;
    }
    setBusy(draft.id); setError(""); setNotice("");
    try {
      const result = await api.createPublishVerification(draft.id, {
        platform_content_id: form.platform_content_id.trim(),
        content_url: form.content_url.trim(),
        published_at: new Date(form.published_at).toISOString(),
        evidence_source: "user_confirmed",
        evidence_note: form.evidence_note.trim(),
      });
      setNotice(`发布核验已保存；独立复盘任务已创建，生产线关闭事件等待任务3消费。`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发布核验失败");
    } finally { setBusy(""); }
  };

  const updateCollection = (taskId: string, key: keyof CollectionForm, value: string) => {
    setCollectionForms((current) => ({
      ...current, [taskId]: { ...(current[taskId] ?? emptyCollection), [key]: value } as CollectionForm,
    }));
  };

  const collect = async (task: ReviewTaskItem) => {
    const form = collectionForms[task.task_id] ?? emptyCollection;
    const metrics: Record<string, number> = {};
    for (const field of METRIC_FIELDS) {
      const raw = form[field].trim();
      if (!raw) continue;
      const value = Number(raw);
      if (!Number.isSafeInteger(value) || value < 0) {
        setError(`${metricLabels[field]}必须是非负整数；留空代表未知，0 代表真实零值。`);
        return;
      }
      metrics[field] = value;
    }
    if (!Object.keys(metrics).length) {
      setError("至少填写一项真实指标；如果平台采集失败，请使用“记录采集失败”。");
      return;
    }
    setBusy(task.task_id); setError(""); setNotice("");
    try {
      await api.collectReviewTask(task.task_id, {
        source: "manual", status: form.status, metrics, force: true,
        measured_at: new Date().toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        idempotency_key: `manual:${task.task_id}:${Date.now()}`,
      });
      setNotice("复盘数据已记录，原始采集批次保留。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "数据补采失败");
    } finally { setBusy(""); }
  };

  const recordFailure = async (task: ReviewTaskItem) => {
    const form = collectionForms[task.task_id] ?? emptyCollection;
    if (!form.error.trim()) { setError("记录采集失败时必须填写失败原因。"); return; }
    setBusy(task.task_id); setError(""); setNotice("");
    try {
      await api.collectReviewTask(task.task_id, {
        source: "platform_readback", status: "failed", metrics: {}, error: form.error.trim(),
        force: true, idempotency_key: `failed:${task.task_id}:${Date.now()}`,
      });
      setNotice("采集失败已留痕；没有把失败转换成 0。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "失败记录提交失败");
    } finally { setBusy(""); }
  };

  const retryClose = async (item: PublishVerificationItem) => {
    if (!item.close_event_id) return;
    setBusy(item.verification_id); setError(""); setNotice("");
    try {
      await api.retryProductionLineClose(item.close_event_id);
      setNotice("生产线关闭事件已重新排队，等待任务3消费。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "生产线关闭重试失败");
    } finally { setBusy(""); }
  };

  const loadRunHistory = async (taskId: string) => {
    try {
      const result = await api.reviewTaskDetail(taskId);
      setRunHistory((current) => ({ ...current, [taskId]: result.task.collection_runs }));
    } catch (err) { setError(err instanceof Error ? err.message : "采集批次加载失败"); }
  };

  const retry = async (task: ReviewTaskItem) => {
    setBusy(task.task_id); setError(""); setNotice("");
    try {
      await api.retryReviewTask(task.task_id);
      setNotice("复盘任务已重新排入待处理队列，历史采集记录保留。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "复盘任务重试失败");
    } finally { setBusy(""); }
  };

  const saveConclusion = async (task: ReviewTaskItem) => {
    const form = conclusionForms[task.task_id] ?? emptyConclusion;
    if (!form.conclusion.trim()) { setError("请填写本次独立复盘结论。"); return; }
    const gapChanges = form.gap_key.trim() && form.gap_title.trim() ? [{
      gap_key: form.gap_key.trim(), title: form.gap_title.trim(),
      description: form.gap_description.trim(), severity: form.severity,
      status: form.gap_status, reason: form.gap_reason.trim(),
      evidence: { task_id: task.task_id, review_status: task.status },
    }] : [];
    setBusy(task.task_id); setError(""); setNotice("");
    try {
      await api.saveReviewConclusion(task.task_id, {
        conclusion: form.conclusion.trim(),
        evidence: {
          recorded_from: "operating_feedback_ui",
          note: form.evidence_note.trim(),
          metric_fact_ids: facts.filter((fact) => fact.content_id === task.content_id).map((fact) => fact.fact_id),
        },
        target_impact: form.target_impact.trim() ? { summary: form.target_impact.trim() } : {},
        recommendations: form.recommendations.split("\n").map((item) => item.trim()).filter(Boolean),
        gap_changes: gapChanges,
      });
      setNotice("复盘结论和经营缺口变更已记录，历史版本不会被覆盖。");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "复盘结论保存失败");
    } finally { setBusy(""); }
  };

  const showGapHistory = async (gapId: string) => {
    try {
      const result = await api.businessGapHistory(gapId);
      setHistory((current) => ({ ...current, [gapId]: result.items }));
    } catch (err) { setError(err instanceof Error ? err.message : "缺口历史加载失败"); }
  };

  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="panel-title">发布核验与独立经营复盘</div>
          <div className="panel-sub">发布证据、延迟采集、失败重试、指标事实和经营缺口在同一处追踪；本模块不会自动发布内容。</div>
        </div>
        <button className="btn sm" type="button" onClick={() => void load()} disabled={loading || !!busy}>刷新</button>
      </div>
      {error && <div className="note" style={{ color: "var(--red)", marginBottom: 8 }}>{error}</div>}
      {notice && <div className="note" style={{ color: "var(--green)", marginBottom: 8 }}>{notice}</div>}
      {loading ? <div className="note">正在加载发布核验和复盘记录…</div> : <>
        <div className="grid kpis" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))", marginBottom: 14 }}>
          <div className="metric"><div className="metric-label">待核验发布</div><div className="metric-val">{needsVerification.length}</div></div>
          <div className="metric"><div className="metric-label">独立复盘任务</div><div className="metric-val">{tasks.length}</div></div>
          <div className="metric"><div className="metric-label">待解决经营缺口</div><div className="metric-val">{gaps.length}</div></div>
          <div className="metric"><div className="metric-label">统一指标事实</div><div className="metric-val">{facts.length}</div></div>
        </div>

        <h3>发布核验</h3>
        {needsVerification.length === 0 ? <div className="note">没有待补充发布证据的已确认内容。</div> : needsVerification.map((draft) => {
          const form = verificationForms[draft.id] ?? {
            platform_content_id: "", content_url: draft.platform_note_url || "",
            published_at: localDateTime(draft.published_at || draft.user_confirmed_at), evidence_note: "",
          };
          return <div key={draft.id} className="panel" style={{ margin: "8px 0", padding: 12 }}>
            <div className="toolbar" style={{ justifyContent: "space-between" }}>
              <strong>{draft.title || draft.id}</strong>
              <span className="tag">{displayPlatformLabel(draft.platform)}</span>
            </div>
            <div className="form-grid" style={{ marginTop: 8 }}>
              <label className="field-label">平台内容 ID
                <input className="field" value={form.platform_content_id} onChange={(e) => updateVerification(draft.id, "platform_content_id", e.target.value)} placeholder="平台返回的笔记/作品 ID" />
              </label>
              <label className="field-label">真实发布时间
                <input className="field" type="datetime-local" value={form.published_at} onChange={(e) => updateVerification(draft.id, "published_at", e.target.value)} />
              </label>
              <label className="field-label" style={{ gridColumn: "1 / -1" }}>平台内容链接
                <input className="field" value={form.content_url} onChange={(e) => updateVerification(draft.id, "content_url", e.target.value)} placeholder="https://…" />
              </label>
              <label className="field-label" style={{ gridColumn: "1 / -1" }}>核验备注（可选）
                <input className="field" value={form.evidence_note} onChange={(e) => updateVerification(draft.id, "evidence_note", e.target.value)} placeholder="例如：平台作品页已可公开访问" />
              </label>
            </div>
            <button className="btn primary sm" style={{ marginTop: 8 }} disabled={!!busy} onClick={() => void submitVerification(draft)}>
              {busy === draft.id ? "保存中…" : "确认真实发布并创建复盘任务"}
            </button>
          </div>;
        })}

        <h3 style={{ marginTop: 18 }}>发布凭证与生产线关闭状态</h3>
        {verifications.length === 0 ? <div className="note">暂无发布核验凭证。</div> : <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead><tr><th>内容</th><th>平台内容 ID</th><th>真实发布时间</th><th>关闭状态</th><th>凭证链接</th></tr></thead>
            <tbody>{verifications.map((item) => <tr key={item.verification_id}>
              <td>{drafts.find((draft) => draft.id === item.draft_id)?.title || item.content_id}</td>
              <td>{item.platform_content_id}</td><td>{new Date(item.published_at).toLocaleString()}</td>
              <td><span className="tag">{statusText(item.closure_status)}</span>{item.closure_error && <div className="panel-sub">{item.closure_error}</div>}
                {item.closure_status === "failed" && item.close_event_id && <button className="btn sm" disabled={!!busy} onClick={() => void retryClose(item)}>重试关闭</button>}
              </td>
              <td><a href={item.content_url} target="_blank" rel="noreferrer">查看作品</a></td>
            </tr>)}</tbody>
          </table>
        </div>}

        <h3 style={{ marginTop: 18 }}>独立复盘任务</h3>
        {tasks.length === 0 ? <div className="note">发布核验完成后，系统会自动建立独立复盘任务。</div> : tasks.map((task) => {
          const form = collectionForms[task.task_id] ?? emptyCollection;
          const conclusion = conclusionForms[task.task_id] ?? emptyConclusion;
          return <div key={task.task_id} className="panel" style={{ margin: "8px 0", padding: 12 }}>
            <div className="toolbar" style={{ justifyContent: "space-between" }}>
              <div><strong>{drafts.find((draft) => draft.id === task.draft_id)?.title || task.content_id}</strong><div className="panel-sub">{displayPlatformLabel(task.platform)} · 计划采集：{new Date(task.scheduled_at).toLocaleString()}</div></div>
              <span className="tag">{statusText(task.status)}</span>
            </div>
            <div className="panel-sub">尝试 {task.attempt_count}/{task.max_attempts} 次 · 最近采集：{task.last_attempt_at ? new Date(task.last_attempt_at).toLocaleString() : "尚未采集"}</div>
            {task.last_error && <div className="panel-sub" style={{ color: "var(--danger)" }}>最近失败/部分采集原因：{task.last_error}</div>}
            <button className="btn sm" style={{ marginTop: 6 }} onClick={() => void loadRunHistory(task.task_id)}>查看采集批次（{runHistory[task.task_id]?.length ?? "加载"}）</button>
            {runHistory[task.task_id] && <div style={{ marginTop: 6 }}>
              {runHistory[task.task_id].map((run, index) => <div key={String(run.run_id || index)} className="panel-sub">
                {statusText(String(run.status || ""))} · {String(run.source || "")} · {String(run.window_start || "—")} 至 {String(run.window_end || "—")}
                {run.error_message ? ` · ${String(run.error_message)}` : ""}
                {run.metrics && <StructuredDataView data={run.metrics} title="本批采集指标"/>}
              </div>)}
            </div>}
            <div className="form-grid" style={{ marginTop: 8 }}>
              {METRIC_FIELDS.map((field) => <label key={field} className="field-label">{metricLabels[field]}
                <input className="field" type="number" min="0" step="1" value={form[field]} onChange={(e) => updateCollection(task.task_id, field, e.target.value)} placeholder="留空表示未知" />
              </label>)}
              <label className="field-label">采集完整度
                <select className="field" value={form.status} onChange={(e) => updateCollection(task.task_id, "status", e.target.value as CollectionForm["status"])}>
                  <option value="partial">部分成功</option><option value="succeeded">完整成功</option>
                </select>
              </label>
              <label className="field-label" style={{ gridColumn: "1 / -1" }}>采集失败原因（失败时必填）
                <input className="field" value={form.error} onChange={(e) => updateCollection(task.task_id, "error", e.target.value)} placeholder="仅在平台无法采集时填写" />
              </label>
            </div>
            <div className="toolbar" style={{ marginTop: 8, flexWrap: "wrap" }}>
              <button className="btn primary sm" disabled={!!busy} onClick={() => void collect(task)}>{busy === task.task_id ? "提交中…" : "提交人工补采数据"}</button>
              <button className="btn sm" disabled={!!busy || !form.error.trim()} onClick={() => void recordFailure(task)}>记录采集失败</button>
              {(task.status === "failed" || task.status === "partial") && <button className="btn sm" disabled={!!busy} onClick={() => void retry(task)}>重新安排采集</button>}
            </div>
            {(task.status === "completed" || task.status === "partial") && <>
              <div className="divider" style={{ margin: "12px 0" }} />
              <strong>复盘结论与经营缺口更新</strong>
              <label className="field-label" style={{ display: "block", marginTop: 8 }}>复盘结论
                <textarea className="field" rows={2} value={conclusion.conclusion} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, conclusion: e.target.value } }))} placeholder="基于采集数据说明目标达成情况、有效因素和问题。" />
              </label>
              <label className="field-label" style={{ display: "block", marginTop: 8 }}>证据说明
                <textarea className="field" rows={2} value={conclusion.evidence_note} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, evidence_note: e.target.value } }))} placeholder="补充数据来源、异常情况或人工核验说明。" />
              </label>
              <label className="field-label" style={{ display: "block", marginTop: 8 }}>目标影响
                <textarea className="field" rows={2} value={conclusion.target_impact} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, target_impact: e.target.value } }))} placeholder="说明本次数据对曝光、互动、转化等目标的影响。" />
              </label>
              <label className="field-label" style={{ display: "block", marginTop: 8 }}>下一轮建议（每行一条）
                <textarea className="field" rows={2} value={conclusion.recommendations} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, recommendations: e.target.value } }))} placeholder="调整选题方向；优化发布时间…" />
              </label>
              <div className="form-grid" style={{ marginTop: 8 }}>
                <label className="field-label">经营缺口标识
                  <input className="field" value={conclusion.gap_key} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, gap_key: e.target.value } }))} placeholder="如 audience-awareness" />
                </label>
                <label className="field-label">缺口标题
                  <input className="field" value={conclusion.gap_title} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, gap_title: e.target.value } }))} placeholder="如目标用户认知不足" />
                </label>
                <label className="field-label">严重程度
                  <select className="field" value={conclusion.severity} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, severity: e.target.value as ConclusionForm["severity"] } }))}>
                    <option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">严重</option>
                  </select>
                </label>
                <label className="field-label">缺口状态
                  <select className="field" value={conclusion.gap_status} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, gap_status: e.target.value as ConclusionForm["gap_status"] } }))}>
                    <option value="active">待解决</option><option value="resolved">已解决</option>
                  </select>
                </label>
                <label className="field-label" style={{ gridColumn: "1 / -1" }}>缺口描述
                  <input className="field" value={conclusion.gap_description} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, gap_description: e.target.value } }))} placeholder="说明缺口表现及其业务影响" />
                </label>
                <label className="field-label" style={{ gridColumn: "1 / -1" }}>缺口变更依据
                  <input className="field" value={conclusion.gap_reason} onChange={(e) => setConclusionForms((current) => ({ ...current, [task.task_id]: { ...conclusion, gap_reason: e.target.value } }))} placeholder="说明为什么新增、更新或关闭该缺口" />
                </label>
              </div>
              <button className="btn sm" style={{ marginTop: 8 }} disabled={!!busy} onClick={() => void saveConclusion(task)}>保存复盘结论与缺口变更</button>
            </>}
          </div>;
        })}

        <h3 style={{ marginTop: 18 }}>当前经营缺口</h3>
        {gaps.length === 0 ? <div className="note">当前没有待解决经营缺口。</div> : gaps.map((gap) => <div key={gap.gap_id} className="toolbar" style={{ justifyContent: "space-between", borderBottom: "1px solid var(--line)", padding: "8px 0" }}>
          <div><strong>{gap.title}</strong><div className="panel-sub">{gap.description} · {gap.severity}</div></div>
          <button className="btn sm" onClick={() => void showGapHistory(gap.gap_id)}>变更历史（{history[gap.gap_id]?.length ?? "查看"}）</button>
          {history[gap.gap_id] && <div style={{ flexBasis: "100%" }}>{history[gap.gap_id].map((event, index) => <div key={index} className="panel-sub">{String(event.event_type)} · {String(event.reason || "")} · {String(event.created_at)}</div>)}</div>}
        </div>)}

        <h3 style={{ marginTop: 18 }}>统一指标事实（最近 30 条）</h3>
        {facts.length === 0 ? <div className="note">暂无指标事实；缺失值不会按 0 处理。</div> : <div style={{ overflowX: "auto" }}>
          <table className="table"><thead><tr><th>指标</th><th>数值</th><th>来源</th><th>采集状态</th><th>统计时间</th></tr></thead>
            <tbody>{facts.map((fact) => <tr key={fact.fact_id}>
              <td>{metricLabels[fact.metric_key as keyof typeof metricLabels] || fact.metric_key}</td>
              <td>{displayMetric(fact.value, fact.unit)}</td><td>{fact.source === "manual" ? "人工补采" : fact.source === "platform_readback" ? "平台回读" : "导入"}</td>
              <td>{statusText(fact.collection_status)}</td><td>{new Date(fact.measured_at).toLocaleString()}</td>
            </tr>)}</tbody>
          </table>
        </div>}
      </>}
    </section>
  );
}
