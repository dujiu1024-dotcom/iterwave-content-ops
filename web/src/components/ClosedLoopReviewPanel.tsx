import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

type ReviewTask = {
  id: string;
  draft_id: string;
  status: string;
  collection_source: string;
  collect_after: string;
  platform_content_id: string;
  collection_result_json: string;
  attempts: number;
  last_error?: string | null;
};

const statusLabel: Record<string, string> = {
  scheduled: "待采集", collecting: "采集中", partial: "部分完成",
  completed: "已完成", failed: "采集失败", cancelled: "已取消",
};

export default function ClosedLoopReviewPanel() {
  const [items, setItems] = useState<ReviewTask[]>([]);
  const [candidates, setCandidates] = useState<Array<{ draft_id: string; platform: string; platform_note_url?: string | null; user_confirmed_at: string; title: string; content_id: string }>>([]);
  const [candidateId, setCandidateId] = useState("");
  const [platformContentId, setPlatformContentId] = useState("");
  const [platformUrl, setPlatformUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [resultText, setResultText] = useState("{}");
  const [status, setStatus] = useState<"partial" | "completed" | "failed">("completed");
  const [errorText, setErrorText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [response, candidateResponse] = await Promise.all([
        api.closedLoopReviewTasks(), api.publishVerificationCandidates(),
      ]);
      setItems(response.items);
      setCandidates(candidateResponse.items);
      setCandidateId((current) => current && candidateResponse.items.some((item) => item.draft_id === current)
        ? current : candidateResponse.items[0]?.draft_id ?? "");
      const firstCandidate = candidateResponse.items.find((item) => item.draft_id === candidateId)
        ?? candidateResponse.items[0];
      if (firstCandidate && !platformUrl) setPlatformUrl(firstCandidate.platform_note_url || "");
      if (firstCandidate && !publishedAt) setPublishedAt(firstCandidate.user_confirmed_at);
      setSelectedId((current) => current && response.items.some((item) => item.id === current)
        ? current : response.items[0]?.id ?? "");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "复盘任务加载失败");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  const verifyPublication = async () => {
    const candidate = candidates.find((item) => item.draft_id === candidateId);
    if (!candidate || !platformContentId.trim() || !platformUrl.trim() || !publishedAt.trim()) {
      setMessage("发布核验需要选择草稿，并填写平台内容 ID、链接和发布时间");
      return;
    }
    setSaving(true); setMessage("");
    try {
      await api.verifyDraftPublication(candidate.draft_id, {
        platform_content_id: platformContentId.trim(),
        platform_url: platformUrl.trim(),
        published_at: publishedAt.trim(),
        content_snapshot: { title: candidate.title, content_id: candidate.content_id, platform: candidate.platform },
      });
      setPlatformContentId("");
      setMessage("发布证据已登记，独立复盘任务已创建");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "发布核验失败");
    } finally { setSaving(false); }
  };

  const submit = async () => {
    if (!selected) return;
    let result: Record<string, unknown> = {};
    try {
      const parsed: unknown = JSON.parse(resultText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("指标必须是 JSON 对象");
      result = parsed as Record<string, unknown>;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "指标 JSON 格式错误");
      return;
    }
    setSaving(true); setMessage("");
    try {
      let snapshotId: string | undefined;
      if (status !== "failed") {
        const metrics: {
          views?: number | null; likes?: number | null; collects?: number | null;
          comments?: number | null; shares?: number | null; measured_at?: string;
        } = {};
        for (const key of ["views", "likes", "collects", "comments", "shares"] as const) {
          if (Object.prototype.hasOwnProperty.call(result, key)) {
            const value = result[key];
            if (value !== null && (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0)) {
              throw new Error(`${key} 必须是非负整数或 null`);
            }
            metrics[key] = value as number | null;
          }
        }
        if (typeof result.measured_at === "string") metrics.measured_at = result.measured_at;
        const snapshot = await api.submitManualMetrics(selected.draft_id, metrics);
        snapshotId = snapshot.snapshot_id;
      }
      const previous = (() => {
        try { return JSON.parse(selected.collection_result_json || "{}") as Record<string, unknown>; }
        catch { return {}; }
      })();
      await api.collectClosedLoopReviewTask(selected.id, {
        result: status === "failed" ? previous : {
          ...previous, latest_metrics: result, latest_metric_snapshot_id: snapshotId,
        },
        status, source: "manual", error: status === "failed" ? errorText.trim() : undefined,
      });
      setMessage(status === "failed" ? "采集失败状态已记录，可在问题解决后重试" : "指标快照和复盘采集记录已保存");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally { setSaving(false); }
  };

  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head">
        <div>
          <div className="panel-title">发布后独立复盘</div>
          <div className="panel-sub">发布核验创建复盘任务；人工补录将写入统一 metric_snapshots 快照。未提供的指标保持 null/缺失，不应填 0。失败记录不写入指标快照。</div>
        </div>
        <button className="btn sm" type="button" onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "刷新任务"}</button>
      </div>
      {message && <div className="note">{message}</div>}
      {candidates.length > 0 && (
        <div className="form-grid" style={{ padding: 14, borderBottom: "1px solid var(--line)" }}>
          <strong>登记已确认发布的内容</strong>
          <label className="field-label">待核验草稿
            <select className="field" value={candidateId} onChange={(event) => {
              const id = event.target.value; setCandidateId(id);
              const candidate = candidates.find((item) => item.draft_id === id);
              setPlatformUrl(candidate?.platform_note_url || "");
              setPublishedAt(candidate?.user_confirmed_at || "");
            }}>
              {candidates.map((item) => <option key={item.draft_id} value={item.draft_id}>
                {item.title} · {item.platform} · {item.user_confirmed_at}
              </option>)}
            </select>
          </label>
          <label className="field-label">平台内容 ID
            <input className="field" value={platformContentId} onChange={(event) => setPlatformContentId(event.target.value)} placeholder="平台作品/笔记 ID" />
          </label>
          <label className="field-label">实际发布链接
            <input className="field" value={platformUrl} onChange={(event) => setPlatformUrl(event.target.value)} placeholder="https://…" />
          </label>
          <label className="field-label">实际发布时间（ISO 8601）
            <input className="field" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} placeholder="2026-11-04T12:30:00+08:00" />
          </label>
          <div><button className="btn primary" type="button" disabled={saving} onClick={() => void verifyPublication()}>{saving ? "核验中…" : "登记发布核验并创建复盘"}</button></div>
        </div>
      )}
      {items.length === 0 ? <div className="note">暂无独立复盘任务。完成发布核验后，系统会自动建立复盘任务。</div> : (
        <div className="form-grid" style={{ padding: 14 }}>
          <label className="field-label">复盘任务
            <select className="field" value={selectedId} onChange={(event) => {
              const id = event.target.value; setSelectedId(id);
              const task = items.find((item) => item.id === id);
              setResultText(task?.collection_result_json || "{}");
              setErrorText(task?.last_error || "");
            }}>
              {items.map((item) => <option key={item.id} value={item.id}>
                {item.draft_id} · {statusLabel[item.status] || item.status} · {item.collect_after}
              </option>)}
            </select>
          </label>
          {selected && <>
            <div className="field-label">平台内容 ID <strong>{selected.platform_content_id}</strong></div>
            <div className="field-label">已尝试采集 <strong>{selected.attempts} 次</strong></div>
            <label className="field-label">采集结果（JSON；仅填写实际获得的指标）
              <textarea className="field" rows={5} value={resultText} onChange={(event) => setResultText(event.target.value)} />
            </label>
            <label className="field-label">采集状态
              <select className="field" value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
                <option value="partial">部分完成</option>
                <option value="completed">已完成</option>
                <option value="failed">采集失败</option>
              </select>
            </label>
            {status === "failed" && <label className="field-label">失败原因
              <input className="field" value={errorText} onChange={(event) => setErrorText(event.target.value)} maxLength={1000} />
            </label>}
            <div><button className="btn primary" type="button" disabled={saving || selected.status === "completed" || selected.status === "cancelled"} onClick={() => void submit()}>{saving ? "保存中…" : "保存复盘采集"}</button></div>
          </>}
        </div>
      )}
    </section>
  );
}
