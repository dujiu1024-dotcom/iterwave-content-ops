import { useEffect, useState } from "react";
import { api, BusinessDiagnosisReport, BusinessDiagnosisVersion } from "../api";
import { formatBusinessDateTime } from "../businessTime.mjs";
import StructuredDataView from "../components/StructuredDataView";
import { displaySourceLabel, displayStatusLabel } from "../displayLabels";

export default function BusinessDiagnosis() {
  const [report, setReport] = useState<BusinessDiagnosisReport | null>(null);
  const [versions, setVersions] = useState<BusinessDiagnosisVersion[]>([]);
  const [editor, setEditor] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ignoreKey, setIgnoreKey] = useState("");
  const [ignoreReason, setIgnoreReason] = useState("");

  async function load() {
    try {
      const [latest, history] = await Promise.all([api.businessDiagnosis(), api.businessDiagnosisVersions()]);
      setReport(latest.report);
      setVersions(history.items);
      setEditor(latest.report ? JSON.stringify(latest.report.report, null, 2) : "");
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "读取失败"); }
  }
  useEffect(() => { void load(); }, []);

  async function analyze() {
    setBusy(true);
    try {
      const result = await api.runBusinessDiagnosis();
      setReport(result.report);
      setEditor(JSON.stringify(result.report.report, null, 2));
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "AI 分析失败"); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!report) return;
    setBusy(true);
    try {
      const result = await api.updateBusinessDiagnosis(report.id, JSON.parse(editor));
      setReport(result.report);
      setEditor(JSON.stringify(result.report.report, null, 2));
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "保存失败，请检查 JSON"); }
    finally { setBusy(false); }
  }
  async function ignoreRecommendation() {
    if (!report || !ignoreKey.trim() || !ignoreReason.trim()) return;
    setBusy(true);
    try {
      const result = await api.ignoreBusinessDiagnosisRecommendation(report.id, ignoreKey.trim(), ignoreReason.trim());
      setReport(result.report); setEditor(JSON.stringify(result.report.report, null, 2));
      setIgnoreKey(""); setIgnoreReason(""); await load();
    } catch (e) { setError(e instanceof Error ? e.message : "忽略建议失败"); }
    finally { setBusy(false); }
  }

  async function confirm() {
    if (!report) return;
    setBusy(true);
    try { const result = await api.confirmBusinessDiagnosis(report.id); setReport(result.report); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "确认失败"); }
    finally { setBusy(false); }
  }

  return <div className="page">
    <div className="page-head"><div><div className="eyebrow">内容生产 · 决策准备</div><h1 className="page-title">AI 经营分析</h1><div className="page-desc">基于经营目标、业务档案、原始素材盘点、内容资产和历史内容形成可追溯报告。确认后才允许创建母计划草稿。</div></div>
      <div className="head-actions"><button className="btn" onClick={() => void load()} disabled={busy}>刷新</button><button className="btn primary" onClick={() => void analyze()} disabled={busy}>{busy ? "处理中…" : "重新分析"}</button></div>
    </div>
    {error && <div className="notice error">{error}</div>}
    <div className="panel">
      <div className="panel-head"><div><div className="panel-title">报告状态</div><div className="panel-sub">源数据变化会使已确认报告失效；重新分析生成新版本。</div></div><span className="tag gray">{report ? displayStatusLabel(report.status) : "尚未分析"}</span></div>
      {report ? <><p>版本 V{report.version_no} · 创建于 {formatBusinessDateTime(report.created_at)}</p>
        <p>来源：经营目标 {report.source_snapshot.inventory_summary.business_goal_count} 项，业务档案 {report.source_snapshot.inventory_summary.registered_business_profile ? "已登记" : "未登记"}，产品/服务目录 {report.source_snapshot.inventory_summary.business_catalog_count ?? 0} 项，原始素材 {report.source_snapshot.inventory_summary.original_asset_count ?? 0} 项（可用 {report.source_snapshot.inventory_summary.original_asset_usable_count ?? 0} 项、待补缺口 {report.source_snapshot.inventory_summary.original_asset_open_gap_count ?? 0} 项），图片素材 {report.source_snapshot.inventory_summary.media_asset_count} 项，历史内容 {report.source_snapshot.inventory_summary.historical_content_count} 条。</p>
        <p>{report.status === "confirmed" ? (report.strategy_is_current ? (report.source_is_current ? "战略来源未变化" : "战略依据有效，生产期数据有更新") : "战略依据已变化，需重新分析") : report.source_is_current ? "来源未变化" : "来源已变化，需重新分析"}</p>
        <div className="panel-sub">分析依据（{report.source_snapshot.source_references.length} 条）</div>
        <div className="table-wrap"><table><thead><tr><th>来源类型</th><th>记录</th></tr></thead><tbody>
          {report.source_snapshot.source_references.slice(0, 100).map((ref, index) => <tr key={ref.source_type + ref.source_id + index}><td>{displaySourceLabel(ref.source_type)}</td><td>{ref.label}</td></tr>)}
          {!report.source_snapshot.source_references.length && <tr><td colSpan={2}>没有可引用的业务资料</td></tr>}
        </tbody></table></div>
        <div className="notice">数据限制：系统已接入 T4 经营基础资料（业务档案及产品/服务目录）和 T5 原始素材盘点（素材状态、审核结果及缺口），分析会基于当前已录入记录。客户访谈库、竞品数据库和外部市场规模数据尚未接入；涉及这些领域的结论应作为假设或待验证事项，不应视为已核实事实。</div>
        <div className="grid two"><div><label className="form-label">忽略建议标识</label><input className="field" value={ignoreKey} onChange={e => setIgnoreKey(e.target.value)} placeholder="例如 recommended_platforms[0]"/></div><div><label className="form-label">忽略原因</label><input className="field" value={ignoreReason} onChange={e => setIgnoreReason(e.target.value)} placeholder="填写业务原因"/></div></div>
        <button className="btn" onClick={() => void ignoreRecommendation()} disabled={busy || !ignoreKey.trim() || !ignoreReason.trim()}>记录忽略建议</button>
        <div className="report-content"><div className="panel-sub">分析结论与建议</div><StructuredDataView data={report.report} title="经营分析报告" /></div>
        <details className="advanced-report-editor"><summary>高级编辑：原始报告数据</summary>
          <p className="tiny">仅在需要精确调整报告结构时使用。修改前请保留字段结构，保存后仍需重新确认。</p>
          <textarea className="field" style={{ width: "100%", minHeight: 320, fontFamily: "monospace" }} value={editor} onChange={e => setEditor(e.target.value)} spellCheck={false}/>
          <button className="btn" onClick={() => void save()} disabled={busy || report.status === "superseded"}>保存高级修改</button>
        </details>
        <div className="plan-form-actions"><button className="btn primary" onClick={() => void confirm()} disabled={busy || report.status !== "draft" || !report.source_is_current}>确认分析报告</button></div>
      </> : <p>尚无报告。请先补充经营资料和素材，再开始 AI 分析。</p>}
    </div>
    <div className="panel"><div className="panel-head"><div><div className="panel-title">历史版本</div><div className="panel-sub">历史报告保留，不覆盖旧版本。</div></div></div>
      <div className="table-wrap"><table><thead><tr><th>版本</th><th>状态</th><th>创建时间</th><th>确认时间</th></tr></thead><tbody>
        {versions.map(v => <tr key={v.id}><td>V{v.version_no}</td><td>{displayStatusLabel(v.status)}</td><td>{formatBusinessDateTime(v.created_at)}</td><td>{formatBusinessDateTime(v.confirmed_at)}</td></tr>)}
        {!versions.length && <tr><td colSpan={4}>暂无历史报告</td></tr>}
      </tbody></table></div>
    </div>
  </div>;
}
