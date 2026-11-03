import { useEffect, useState } from "react";
import { api, BusinessCatalogItem, BusinessProfile } from "../api";
import { useApp } from "../AppContext";

type EntityType = "product" | "service" | "audience" | "market";
const tabs: Array<{ key: EntityType; label: string }> = [
  { key: "product", label: "产品" }, { key: "service", label: "服务" },
  { key: "audience", label: "目标客户" }, { key: "market", label: "市场信息" },
];
const emptyProfile: BusinessProfile = {
  business_name: "", introduction: "", industry: "", business_model: "",
  business_stage: "", operating_goal: "", field_evidence: {}, version_no: 0,
  updated_at: null,
};

export default function BusinessProfilePage() {
  const { showToast } = useApp();
  const [profile, setProfile] = useState<BusinessProfile>(emptyProfile);
  const [items, setItems] = useState<BusinessCatalogItem[]>([]);
  const [tab, setTab] = useState<EntityType>("product");
  const [name, setName] = useState("");
  const [details, setDetails] = useState("{}");
  const [editing, setEditing] = useState<string | null>(null);
  const [editingEvidence, setEditingEvidence] = useState<BusinessCatalogItem["field_evidence"]>({});
  const [analysis, setAnalysis] = useState<{ missing: Array<{ field: string; label: string }>; completeness: number } | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [proposals, setProposals] = useState<Array<Record<string, unknown>>>([]);
  const [versions, setVersions] = useState<Array<{version_no:number;change_note:string;created_at:string;created_by:string}>>([]);
  const [selectedVersion, setSelectedVersion] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true); setError("");
    try {
      const [p, catalog, check, history] = await Promise.all([
        api.businessProfile(), api.businessItems(), api.businessAnalysis(), api.businessVersions(),
      ]);
      setProfile(p); setItems(catalog.items); setAnalysis(check); setVersions(history.items);
    } catch (e) { setError(e instanceof Error ? e.message : "业务资料加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const saveProfile = async () => {
    setBusy(true);
    try {
      const saved = await api.saveBusinessProfile(profile);
      setProfile(saved); showToast("业务概况已保存", "success"); await load();
    } catch (e) { showToast("保存失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
    finally { setBusy(false); }
  };

  const resetEditor = () => { setEditing(null); setEditingEvidence({}); setName(""); setDetails("{}"); };
  const editItem = (item: BusinessCatalogItem) => {
    setEditing(item.id); setEditingEvidence(item.field_evidence); setName(item.name); setDetails(JSON.stringify(item.details, null, 2));
  };
  const saveItem = async () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(details);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error();
    } catch { showToast("资料详情必须是有效的 JSON 对象", "error"); return; }
    setBusy(true);
    try {
      await api.saveBusinessItem(tab, { name, details: parsed, field_evidence: editingEvidence }, editing ?? undefined);
      resetEditor(); await load(); showToast("业务资料已保存", "success");
    } catch (e) { showToast("保存失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
    finally { setBusy(false); }
  };
  const archiveItem = async (id: string) => {
    if (!window.confirm("确认归档这条业务资料？历史计划快照不会受到影响。")) return;
    try { await api.archiveBusinessItem(id); await load(); }
    catch (e) { showToast("归档失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };

  const visibleItems = items.filter(item => item.entity_type === tab);
  if (loading) return <div className="page"><div className="page-header"><h1>经营资料</h1></div><p>正在加载业务档案…</p></div>;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>经营资料</h1><p>先明确经营什么、卖给谁，再制定战略计划。业务资料与计划版本独立管理。</p></div>
        <button className="btn" onClick={() => void load()}>刷新</button>
      </div>
      {error && <div className="notice error">{error}</div>}
      <section className="panel">
        <div className="panel-header"><h2>业务概况</h2><span className="muted">档案版本 v{profile.version_no}</span></div>
        <div className="form-grid">
          <label>业务名称<input className="field" value={profile.business_name} onChange={e => setProfile({...profile,business_name:e.target.value})} /></label>
          <label>所属行业<input className="field" value={profile.industry} onChange={e => setProfile({...profile,industry:e.target.value})} /></label>
          <label>商业模式<input className="field" value={profile.business_model} onChange={e => setProfile({...profile,business_model:e.target.value})} placeholder="例如：产品销售、订阅、项目交付" /></label>
          <label>当前业务阶段<input className="field" value={profile.business_stage} onChange={e => setProfile({...profile,business_stage:e.target.value})} placeholder="例如：验证期、增长期、成熟期" /></label>
          <label className="full">业务介绍<textarea className="field" rows={3} value={profile.introduction} onChange={e => setProfile({...profile,introduction:e.target.value})} /></label>
          <label className="full">经营目标<textarea className="field" rows={2} value={profile.operating_goal} onChange={e => setProfile({...profile,operating_goal:e.target.value})} placeholder="例如：提高复购率、拓展某类客户、验证新产品" /></label>
        </div>
        <div className="muted">字段来源：{Object.entries(profile.field_evidence).map(([field, evidence]) => `${field}：${({user_confirmed:"用户确认",external_source:"外部来源",ai_inferred:"AI 推测",missing:"尚未提供"} as Record<string,string>)[evidence.status] || evidence.status}`).join("；") || "尚未标注"}</div>
        <div className="actions"><button className="btn primary" disabled={busy} onClick={() => void saveProfile()}>保存业务概况</button></div>
      </section>

      <section className="panel">
        <div className="panel-header"><h2>经营资料版本</h2><span className="muted">每次业务概况或目录变更都会形成完整快照</span></div>
        {versions.length ? <div className="catalog-list">{versions.map(v=><article className="catalog-item" key={v.version_no}><div><strong>业务资料 v{v.version_no}</strong><small className="muted">{v.change_note || "资料变更"} · {new Date(v.created_at).toLocaleString()}</small></div><button className="btn sm" onClick={async()=>{try{setSelectedVersion(await api.businessVersion(v.version_no));}catch(e){showToast((e as Error).message,"error");}}}>查看快照</button></article>)}</div> : <p className="muted">尚无历史版本，首次保存后自动建立。</p>}
        {selectedVersion && <pre>{JSON.stringify(selectedVersion.snapshot,null,2)}</pre>}
      </section>

      <section className="panel">
        <div className="panel-header"><h2>经营资料库</h2><span className="muted">产品、服务、客户和市场资料分别维护</span></div>
        <div className="tabs">{tabs.map(t => <button key={t.key} className={tab===t.key?"tab active":"tab"} onClick={() => {setTab(t.key);resetEditor();}}>{t.label}</button>)}</div>
        <div className="catalog-list">
          {visibleItems.length === 0 ? <p className="muted">暂无{tabs.find(t=>t.key===tab)?.label}资料。</p> : visibleItems.map(item => (
            <article className="catalog-item" key={item.id}>
              <div><strong>{item.name}</strong><pre>{JSON.stringify(item.details,null,2)}</pre><small className="muted">来源：{Object.entries(item.field_evidence).map(([field,evidence])=>`${field}：${({user_confirmed:"用户确认",external_source:"外部来源",ai_inferred:"AI 推测",missing:"尚未提供"} as Record<string,string>)[evidence.status] || evidence.status}`).join("；") || "未标注"}</small></div>
              <div className="actions"><button className="btn sm" onClick={() => editItem(item)}>编辑</button><button className="btn sm danger" onClick={() => void archiveItem(item.id)}>归档</button></div>
            </article>
          ))}
        </div>
        <div className="editor">
          <h3>{editing ? "编辑资料" : "新增资料"}</h3>
          <label>名称<input className="field" value={name} onChange={e=>setName(e.target.value)} placeholder="填写名称" /></label>
          <label>结构化详情（JSON）<textarea className="field code-field" rows={7} value={details} onChange={e=>setDetails(e.target.value)} /></label>
          <div className="actions"><button className="btn primary" disabled={busy || !name.trim()} onClick={() => void saveItem()}>保存</button><button className="btn" onClick={resetEditor}>取消</button></div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header"><h2>资料完整性检查</h2><strong>{analysis?.completeness ?? 0}%</strong></div>
        {analysis?.missing.length ? <ul>{analysis.missing.map(item=><li key={item.field}>待补充：{item.label}</li>)}</ul> : <p>基础经营资料已填写完整。</p>}
        <p className="muted">当前检查仅识别缺失项，不会把推测内容写入正式档案。AI 整理建议必须经过人工确认。</p>
        <p className="muted">请勿粘贴密码、访问令牌、个人身份信息等敏感资料。AI 只返回待确认建议，不会自动修改档案。</p>
        <label>粘贴原始经营资料<textarea className="field" rows={4} value={sourceText} onChange={e=>setSourceText(e.target.value)} placeholder="粘贴产品介绍、服务说明、客户访谈、竞品观察等原始资料" /></label>
        <div className="actions"><button className="btn" disabled={!sourceText.trim() || busy} onClick={async()=>{try{setBusy(true);const r=await api.businessAiProposal(sourceText);setProposals(r.proposals || []);showToast(r.message,"info");}catch(e){showToast((e as Error).message,"error");}finally{setBusy(false);}}}>AI 整理资料</button></div>
        {proposals.map((proposal,index)=><article className="catalog-item" key={index}><div><strong>{String(proposal.name || proposal.entity_type)}</strong><pre>{JSON.stringify(proposal,null,2)}</pre></div><button className="btn sm primary" onClick={async()=>{try{await api.confirmBusinessAiProposal(proposal);setProposals(items=>items.filter((_,i)=>i!==index));await load();showToast("已确认并写入业务档案","success");}catch(e){showToast((e as Error).message,"error");}}}>确认写入</button></article>)}
      </section>
    </div>
  );
}
