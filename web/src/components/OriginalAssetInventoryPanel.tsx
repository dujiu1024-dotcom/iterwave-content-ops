import StructuredDataView from "./StructuredDataView";
import { useEffect, useState } from "react";
import { api, OriginalAsset, OriginalAssetGap, OriginalAssetSummary } from "../api";
import { useApp } from "../AppContext";
import { formatBusinessDateTime } from "../businessTime.mjs";
import { displayStatusLabel } from "../displayLabels";

const assetTypes = [
  ["product_image", "产品图片"], ["product_description", "产品介绍"],
  ["product_manual", "产品说明书"], ["technical_document", "技术文档"], ["customer_case", "客户案例"],
  ["customer_review", "客户评价"], ["historical_article", "历史文章"],
  ["brand_material", "品牌资料"], ["credential", "资质证明"], ["other", "其他原始资料"],
];
const typeLabel = (value: string) => assetTypes.find(([key]) => key === value)?.[1] ?? "其他素材";
const statusLabel: Record<string, string> = { pending: "待确认", confirmed: "已确认", rejected: "已驳回", open: "待补充", in_progress: "补充中", fulfilled: "已完成", waived: "已豁免", running: "分析中", needs_review: "待人工确认", failed: "分析失败" };
const statusText = (value: string) => statusLabel[value] ?? "其他状态";
const warningText = (value: string) => ({
  TimeoutException: "分析服务响应超时",
  ConnectError: "分析服务暂时无法连接",
  ConnectTimeout: "分析服务连接超时",
  ReadTimeout: "分析服务响应超时",
  HTTPStatusError: "分析服务暂时不可用",
  JSONDecodeError: "分析结果格式无法识别",
  ValueError: "分析结果格式不符合要求",
}[value] ?? "部分分析服务未能完成");
const entityTypeLabel: Record<string, string> = { business: "企业业务", product: "产品", service: "服务", customer_case: "客户案例", content_topic: "内容主题", platform: "平台", plan: "计划" };
const relationTypeLabel: Record<string, string> = { related: "关联", source: "来源", supports: "支持", derived_from: "派生自" };
function askQualityChoice(title: string, choices: Array<[string, string]>, fallback: string): string | null {
  const options = choices.map(([value, label], index) => `${index + 1}. ${label}`).join("\n");
  const answer = window.prompt(`${title}\n${options}\n请输入序号`, String(Math.max(0, choices.findIndex(([value]) => value === fallback)) + 1));
  if (answer === null) return null;
  const index = Number(answer.trim()) - 1;
  return Number.isInteger(index) && index >= 0 && index < choices.length ? choices[index][0] : null;
}
const qualityLabel: Record<string, string> = {
  unknown: "待核实", readable: "可读取", unreadable: "无法读取", not_applicable: "不适用",
  complete: "完整", incomplete: "不完整", allowed: "允许商业使用", restricted: "使用受限",
  prohibited: "禁止商业使用", clear: "未发现隐私风险", contains_personal_data: "含个人信息",
};

export default function OriginalAssetInventoryPanel() {
  const { showToast } = useApp();
  const [summary, setSummary] = useState<OriginalAssetSummary | null>(null);
  const [assets, setAssets] = useState<OriginalAsset[]>([]);
  const [gaps, setGaps] = useState<OriginalAssetGap[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [assetType, setAssetType] = useState("");
  const [showRegister, setShowRegister] = useState(false);
  const [showGap, setShowGap] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<{ assetId: number; id: number; result: Record<string, unknown> } | null>(null);
  const [history, setHistory] = useState<{ asset: OriginalAsset; analyses: Array<{ id: number; asset_version_no: number; analysis_type: string; status: string; result: Record<string, unknown>; warnings: string[]; provider?: string | null; model?: string | null; created_at: string }>; versions: Array<{ version_no: number; title: string; description: string; review_status: string; commercial_use_status: string; created_at: string }>; links: Array<{ id: number; entity_type: string; entity_id: string; relation_type: string }> } | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [sourceKind, setSourceKind] = useState("manual");
  const [sourceUri, setSourceUri] = useState("");
  const [linkEntityType, setLinkEntityType] = useState("product");
  const [linkEntityId, setLinkEntityId] = useState("");
  const [gapEntityType, setGapEntityType] = useState("product");
  const [gapTitle, setGapTitle] = useState("");
  const [gapEntityId, setGapEntityId] = useState("");
  const [gapAssetType, setGapAssetType] = useState("product_image");
  const [gapReason, setGapReason] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [s, a, g] = await Promise.all([
        api.originalAssetsSummary(),
        api.originalAssets({ query, asset_type: assetType || undefined }),
        api.originalAssetGaps("all"),
      ]);
      setSummary(s); setAssets(a.items); setGaps(g.items);
    } catch {
      showToast("素材盘点读取失败，请稍后刷新重试", "error");
    } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [query, assetType]);

  const syncExisting = async () => {
    try {
      const result = await api.syncOriginalAssets();
      showToast("已同步 " + result.total_created + " 条引用（图片 " + result.created_media_assets + "，内容资产 " + result.created_content_assets + "，知识库来源 " + (result.created_kb_sources ?? 0) + "）", "success");
      await load();
    } catch { showToast("素材同步失败，请稍后重试", "error"); }
  };
  const register = async () => {
    if (!title.trim()) return showToast("请填写素材名称", "error");
    if (sourceKind === "external" && !sourceUri.trim()) return showToast("外部素材必须填写来源地址", "error");
    try {
      const created = await api.registerOriginalAsset({
        asset_type: assetType || "other", title: title.trim(), description,
        source_kind: sourceKind, source_uri: sourceKind === "external" ? sourceUri.trim() : null,
      });
      if (linkEntityId.trim()) {
        await api.linkOriginalAsset(created.id, {
          entity_type: linkEntityType, entity_id: linkEntityId.trim(),
        });
      }
      setTitle(""); setDescription(""); setSourceUri(""); setLinkEntityId(""); setShowRegister(false);
      showToast("素材登记成功，当前状态为待确认", "success"); await load();
    } catch { showToast("素材登记失败，请检查必填信息后重试", "error"); }
  };
  const review = async (asset: OriginalAsset) => {
    const permission = askQualityChoice("商业使用权限", [["allowed", "允许商业使用"], ["restricted", "使用受限"], ["prohibited", "禁止商业使用"]], asset.commercial_use_status === "unknown" ? "allowed" : asset.commercial_use_status);
    if (!permission) return;
    const privacy = askQualityChoice("隐私分类", [["clear", "未发现隐私风险"], ["not_applicable", "不适用"], ["contains_personal_data", "含个人信息"], ["restricted", "受限信息"]], "clear");
    if (!privacy) return;
    const readability = askQualityChoice("可读取性", [["readable", "可读取"], ["not_applicable", "不适用"], ["unreadable", "无法读取"]], "readable");
    if (!readability) return;
    const completeness = askQualityChoice("完整度", [["complete", "完整"], ["incomplete", "不完整"]], "complete");
    if (!completeness) return;
    const duplicate = askQualityChoice("重复检查", [["unique", "未发现重复"], ["possible_duplicate", "可能重复"], ["duplicate", "确认重复"]], "unique");
    if (!duplicate) return;
    const decision = askQualityChoice("审核结论", [["confirmed", "确认通过"], ["rejected", "驳回"]], "confirmed");
    if (!decision) return;
    try {
      await api.reviewOriginalAsset(asset.id, {
        decision, review_note: "人工审核",
        quality: { commercial_use_status: permission, privacy_status: privacy, readability_status: readability, completeness_status: completeness, duplicate_status: duplicate },
      });
      showToast("素材审核已保存", "success"); await load();
    } catch { showToast("素材审核失败，请检查填写内容后重试", "error"); }
  };
  const analyze = async (asset: OriginalAsset) => {
    const analysisType = asset.asset_type === "product_image"
      ? "image_product_extraction"
      : asset.asset_type === "customer_case"
        ? "case_structuring"
        : "document_value_extraction";
    try {
      const result = await api.analyzeOriginalAsset(asset.id, analysisType);
      if (result.result) setAnalysisResult({ assetId: asset.id, id: result.id, result: result.result });
      showToast("AI 分析完成，结果待人工确认", "success");
    } catch { showToast("AI 素材分析失败，请检查素材可读性和模型配置后重试", "error"); }
  };
  const reviewAnalysis = async (decision: "confirmed" | "rejected") => {
    if (!analysisResult) return;
    try {
      await api.reviewOriginalAssetAnalysis(analysisResult.assetId, analysisResult.id, decision);
      showToast(decision === "confirmed" ? "AI 分析结果已人工确认" : "AI 分析结果已驳回", "success");
      setAnalysisResult(null);
    } catch { showToast("分析审核失败，请刷新记录后重试", "error"); }
  };
  const showHistory = async (asset: OriginalAsset) => {
    try {
      const [analyses, versions, links] = await Promise.all([
        api.originalAssetAnalyses(asset.id), api.originalAssetVersions(asset.id), api.originalAssetLinks(asset.id),
      ]);
      setHistory({ asset, analyses: analyses.items, versions: versions.items, links: links.items });
    } catch {
      showToast("素材历史读取失败，请稍后重试", "error");
    }
  };
  const createGap = async () => {
    if (!gapTitle.trim() || !gapEntityId.trim()) return showToast("请填写缺口名称和关联对象 ID", "error");
    try {
      await api.createOriginalAssetGap({
        entity_type: gapEntityType, entity_id: gapEntityId.trim(), asset_type: gapAssetType,
        title: gapTitle.trim(), reason: gapReason.trim(), priority: "normal",
      });
      setGapTitle(""); setGapEntityId(""); setGapReason(""); setShowGap(false);
      showToast("素材缺口已登记", "success"); await load();
    } catch { showToast("缺口登记失败，请检查关联对象后重试", "error"); }
  };
  const fulfillGap = async (gap: OriginalAssetGap) => {
    const raw = window.prompt("输入已确认且允许商业使用的素材 ID");
    if (!raw || !/^\d+$/.test(raw)) return;
    try {
      await api.updateOriginalAssetGap(gap.id, { status: "fulfilled", resolved_asset_id: Number(raw) });
      showToast("素材缺口已关联并完成", "success"); await load();
    } catch { showToast("素材关联失败，请核对素材状态后重试", "error"); }
  };

  return <section className="panel original-asset-inventory">
    <div className="panel-head">
      <div><div className="panel-title">原始素材盘点</div><div className="panel-sub">登记已有素材、确认授权与质量，并记录尚未具备的素材需求。此处不复制原始文件。</div></div>
      <div className="head-actions"><button className="btn" onClick={() => void syncExisting()} disabled={loading}>同步现有资产</button><button className="btn" onClick={() => void load()} disabled={loading}>刷新</button><button className="btn" onClick={() => setShowGap(v => !v)}>＋ 素材缺口</button><button className="btn primary" onClick={() => setShowRegister(v => !v)}>＋ 登记素材</button></div>
    </div>
    <div className="grid four" style={{ marginBottom: 14 }}>
      <div className="plan-summary-card"><div className="tiny">素材总量</div><strong>{summary?.total ?? "—"}</strong></div>
      <div className="plan-summary-card"><div className="tiny">待确认</div><strong>{summary?.by_review_status.pending ?? 0}</strong></div>
      <div className="plan-summary-card"><div className="tiny">已确认</div><strong>{summary?.by_review_status.confirmed ?? 0}</strong></div>
      <div className="plan-summary-card"><div className="tiny">未完成缺口</div><strong>{(summary?.gaps.open ?? 0) + (summary?.gaps.in_progress ?? 0)}</strong></div>
    </div>
    <div className="tiny" style={{ marginBottom: 10 }}>按类型：{Object.entries(summary?.by_type ?? {}).map(([key, count]) => typeLabel(key) + " " + count).join(" · ") || "暂无数据"}</div>
    <div className="tiny" style={{ marginBottom: 14 }}>按产品：{Object.entries(summary?.by_product ?? {}).map(([key, count]) => key + " " + count).join(" · ") || "尚未关联产品"}</div>
    {showRegister && <div className="plan-stage-editor">
      <div className="panel-head"><b>登记原始素材</b><button className="btn sm" onClick={() => setShowRegister(false)}>取消</button></div>
      <div className="grid two">
        <div><label className="form-label">素材名称</label><input className="field" value={title} onChange={e => setTitle(e.target.value)} placeholder="例如：产品核心功能介绍"/>
          <label className="form-label">素材类型</label><select className="field" value={assetType} onChange={e => setAssetType(e.target.value)}>{assetTypes.map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div><label className="form-label">素材来源</label><select className="field" value={sourceKind} onChange={e => setSourceKind(e.target.value)}><option value="manual">手工登记</option><option value="external">外部链接</option></select>
          {sourceKind === "external" && <><label className="form-label">来源地址</label><input className="field" value={sourceUri} onChange={e => setSourceUri(e.target.value)} placeholder="https://…"/></>}</div>
      </div>
      <div className="grid two"><div><label className="form-label">关联业务对象</label><select className="field" value={linkEntityType} onChange={e => setLinkEntityType(e.target.value)}><option value="product">产品</option><option value="service">服务</option><option value="business">企业业务</option><option value="customer_case">客户案例</option><option value="content_topic">内容主题</option><option value="platform">平台</option><option value="plan">计划</option></select></div><details className="advanced-report-editor"><summary>高级关联：业务对象标识（可选）</summary><input aria-label="业务对象标识" className="field" value={linkEntityId} onChange={e => setLinkEntityId(e.target.value)} placeholder="填写关联对象标识"/></details></div>
      <label className="form-label">素材说明</label><textarea className="field" rows={2} value={description} onChange={e => setDescription(e.target.value)} placeholder="用途、适用产品、注意事项等"/>
      <div className="plan-form-actions"><button className="btn primary" onClick={() => void register()}>保存登记</button></div>
    </div>}
    {analysisResult && <div className="plan-stage-editor">
      <div className="panel-head"><b>AI 素材分析结果 · 待人工确认</b><button className="btn sm" onClick={() => setAnalysisResult(null)}>关闭</button></div>
      <StructuredDataView data={analysisResult.result} title="分析结果" />
      <div className="plan-form-actions"><button className="btn" onClick={() => void reviewAnalysis("rejected")}>驳回分析结果</button><button className="btn primary" onClick={() => void reviewAnalysis("confirmed")}>确认分析结果</button></div>
    </div>}
    {history && <div className="plan-stage-editor">
      <div className="panel-head"><b>素材历史：{history.asset.title}</b><button className="btn sm" onClick={() => setHistory(null)}>关闭</button></div>
      <b>版本记录</b>
      {history.versions.length === 0 ? <div className="tiny">暂无版本记录</div> : <div className="list">{history.versions.map(v => <div className="task-row" key={v.version_no}><div className="task-main"><b>v{v.version_no} · {v.title}</b><div className="tiny">{formatBusinessDateTime(v.created_at)} · {statusLabel[v.review_status] ?? displayStatusLabel(v.review_status)} · 商业权限：{displayStatusLabel(v.commercial_use_status)}</div><div className="tiny">{v.description || "无说明"}</div></div></div>)}</div>}
      <b style={{ display: "block", marginTop: 12 }}>AI 分析记录</b>
      {history.analyses.length === 0 ? <div className="tiny">暂无分析记录</div> : <div className="list">{history.analyses.map(item => <details className="task-row" key={item.id}><summary><b>{item.analysis_type === "quality" ? "质量分析" : item.analysis_type === "classification" ? "素材分类" : "素材分析"}</b> · v{item.asset_version_no} · {statusText(item.status)} · {formatBusinessDateTime(item.created_at)}</summary><StructuredDataView data={item.result} title="历史分析结果" />{item.warnings?.length > 0 && <div className="tiny">提示：{item.warnings.map(warningText).join("；")}</div>}</details>)}</div>}
      <b style={{ display: "block", marginTop: 12 }}>业务对象关联</b>
      {history.links.length === 0 ? <div className="tiny">暂无关联</div> : <div className="tiny">{history.links.map(link => (entityTypeLabel[link.entity_type] ?? "业务对象") + "（" + (relationTypeLabel[link.relation_type] ?? "其他关系") + "）").join(" · ")}</div>}
    </div>}
    {showGap && <div className="plan-stage-editor">
      <div className="panel-head"><b>登记素材缺口</b><button className="btn sm" onClick={() => setShowGap(false)}>取消</button></div>
      <div className="grid two"><div><label className="form-label">关联对象类型</label><select className="field" value={gapEntityType} onChange={e => setGapEntityType(e.target.value)}><option value="product">产品</option><option value="service">服务</option><option value="business">企业业务</option><option value="customer_case">客户案例</option><option value="content_topic">内容主题</option><option value="platform">平台</option><option value="plan">计划</option></select><label className="form-label">对象 ID</label><input className="field" value={gapEntityId} onChange={e => setGapEntityId(e.target.value)} placeholder="填写关联对象 ID"/>
        <label className="form-label">缺失素材类型</label><select className="field" value={gapAssetType} onChange={e => setGapAssetType(e.target.value)}>{assetTypes.map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div><label className="form-label">缺口名称</label><input className="field" value={gapTitle} onChange={e => setGapTitle(e.target.value)} placeholder="例如：产品详情图"/>
          <label className="form-label">缺失原因</label><textarea className="field" rows={2} value={gapReason} onChange={e => setGapReason(e.target.value)}/></div></div>
      <div className="plan-form-actions"><button className="btn primary" onClick={() => void createGap()}>保存缺口</button></div>
    </div>}
    <div className="form-row"><input className="field" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索素材名称或说明"/>
      <select className="field" value={assetType} onChange={e => setAssetType(e.target.value)}><option value="">全部素材类型</option>{assetTypes.map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div>
    {loading ? <div className="loading">正在读取素材盘点…</div> : assets.length === 0 ? <div className="empty-hint">尚无登记素材。可以先从现有图片库和内容资产建立引用登记。</div> :
      <div className="table-wrap"><table><thead><tr><th>素材</th><th>类型</th><th>来源</th><th>版本</th><th>质量与授权</th><th>审核</th><th>操作</th></tr></thead><tbody>
        {assets.map(asset => <tr key={asset.id}><td><b>{asset.title}</b><div className="tiny">{asset.description || "暂无说明"}</div></td><td>{typeLabel(asset.asset_type)}</td><td>{asset.source_kind === "media_asset" ? "图片库" : asset.source_kind === "content_asset" ? "内容资产" : asset.source_kind === "kb_source" ? "知识库" : asset.source_kind === "external" ? "外部链接" : "手工登记"}</td><td>v{asset.current_version_no}</td><td><div className="tiny">可读性：{(qualityLabel[asset.readability_status] ?? "待核实")} · 完整度：{(qualityLabel[asset.completeness_status] ?? "待核实")}</div><div className="tiny">商业权限：{(qualityLabel[asset.commercial_use_status] ?? "待核实")} · 隐私：{(qualityLabel[asset.privacy_status] ?? "待核实")}</div></td><td>{statusText(asset.review_status)}</td><td><button className="btn sm" onClick={() => void analyze(asset)}>AI 分析</button> <button className="btn sm" onClick={() => void showHistory(asset)}>历史</button> <button className="btn sm" disabled={asset.review_status === "confirmed"} onClick={() => void review(asset)}>审核</button></td></tr>)}
      </tbody></table></div>}
    <div className="panel-head" style={{ marginTop: 20 }}><div><div className="panel-title">素材缺口清单</div><div className="panel-sub">缺口在关联到已确认、允许商业使用的素材之前，不会自动关闭。</div></div></div>
    {gaps.length === 0 ? <div className="empty-hint">暂无登记缺口</div> : <div className="list">{gaps.map(gap => <div className="task-row" key={gap.id}><div className="task-main"><div className="task-title">{gap.title} <span className="tag gray">{statusText(gap.status)}</span></div><div className="task-desc">{({ product: "产品", service: "服务", business: "企业业务", customer_case: "客户案例", content_topic: "内容主题", platform: "平台", plan: "计划" } as Record<string, string>)[gap.entity_type] ?? "关联业务对象"} · {typeLabel(gap.asset_type)} · {gap.reason || "未填写原因"}</div></div>{gap.status !== "fulfilled" && gap.status !== "waived" && <button className="btn sm" onClick={() => void fulfillGap(gap)}>关联已有素材</button>}</div>)}</div>}
  </section>;
}
