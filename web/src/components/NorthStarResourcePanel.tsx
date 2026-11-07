import { useEffect, useState } from "react";
import { api } from "../api";
import { formatBusinessDateTime } from "../businessTime.mjs";
import { displaySourceLabel } from "../displayLabels";

const fields = [
  ["positioning", "账号组定位"],
  ["business_direction", "经营方向"],
  ["target_users", "目标用户"],
  ["core_value", "核心价值"],
  ["long_term_content_direction", "长期内容方向"],
  ["business_model", "经营模式"],
  ["platform_relationships", "平台关系"],
  ["long_term_goals", "长期目标"],
] as const;

export default function NorthStarResourcePanel({ onOpenAssets }: { onOpenAssets?: () => void }) {
  const [snapshot,setSnapshot] = useState<Record<string,string>>({});
  const [reason,setReason] = useState("");
  const [history,setHistory] = useState<Array<{id:number;version_no:number;status:string;change_reason:string;created_at:string;created_by:string}>>([]);
  const [resources,setResources] = useState<Array<{category:string;title:string;source_type:string;source_id:string;status:string;updated_at:string|null}>>([]);
  const [gaps,setGaps] = useState<Array<Record<string,unknown>>>([]);
  const [summary,setSummary] = useState<Record<string,number>>({});
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [selectedVersion,setSelectedVersion] = useState<{version_no:number;snapshot:Record<string,string>} | null>(null);

  const load = async () => {
    setError("");
    try {
      const [north,versions,baseline] = await Promise.all([api.northStar(),api.northStarVersions(),api.resourceBaseline()]);
      setSnapshot(north.current?.snapshot ?? {});
      setHistory(versions.items);
      setResources(baseline.resources);
      setGaps(baseline.gaps);
      setSummary(baseline.summary);
    } catch(e) { setError("北极星数据操作失败，请稍后重试"); }
  };
  useEffect(() => { void load(); }, []);

  const save = async () => {
    setBusy(true); setError("");
    try {
      await api.saveNorthStar({...snapshot,change_reason:reason});
      setReason("");
      await load();
    } catch(e) { setError("北极星数据操作失败，请稍后重试"); }
    finally { setBusy(false); }
  };

  const labels:Record<string,string> = {total:"资源条目",confirmed:"已确认",pending:"待审核",restricted:"受限",rejected:"已拒绝",missing:"缺失",unknown:"未接入",gaps:"资源缺口"};
  const statuses:Record<string,string> = {confirmed:"已确认且允许使用",pending:"待审核",restricted:"使用受限",rejected:"已拒绝/禁止使用",missing:"缺失",unknown:"未接入"};
  return <div className="stack">
    <section className="panel">
      <div className="panel-head"><div><div className="panel-title">账号组北极星</div><div className="panel-sub">独立于目标进度树的长期定位档案。每次保存都会建立新版本，旧版本保留。</div></div><span className="tag gray">{history.length ? "当前版本 V"+(history.find(x=>x.status==="effective")?.version_no ?? "—") : "尚未建立"}</span></div>
      <div className="grid two">
        {fields.map(([key,label]) => <div key={key}><label className="form-label">{label}</label><textarea className="field" rows={key==="long_term_content_direction" || key==="long_term_goals" ? 3 : 2} value={snapshot[key] ?? ""} onChange={e=>setSnapshot(old=>({...old,[key]:e.target.value}))}/></div>)}
      </div>
      <label className="form-label" style={{marginTop:12}}>本次修改原因</label>
      <input className="field" value={reason} onChange={e=>setReason(e.target.value)} placeholder="说明定位或经营方向为何调整"/>
      {error && <div className="note" role="alert">{error}</div>}
      <div className="plan-form-actions"><button className="btn primary" disabled={busy || !reason.trim()} onClick={()=>void save()}>{busy ? "保存中…" : "保存并创建新版本"}</button></div>
    </section>
    <section className="panel">
      <div className="panel-head"><div><div className="panel-title">北极星版本历史</div><div className="panel-sub">母计划绑定的是创建时的北极星版本，不会随当前版本变化。</div></div></div>
      {history.length ? <div className="table-wrap"><table><thead><tr><th>版本</th><th>状态</th><th>修改原因</th><th>操作者</th><th>时间</th><th>快照</th></tr></thead><tbody>{history.map(v=><tr key={v.id}><td>V{v.version_no}</td><td>{v.status==="effective"?"当前生效":"历史版本"}</td><td>{v.change_reason}</td><td>{v.created_by}</td><td>{formatBusinessDateTime(v.created_at)}</td><td><button className="btn sm" onClick={async()=>{try{const item=await api.northStarVersion(v.id);setSelectedVersion({version_no:item.version_no,snapshot:item.snapshot});}catch(e){setError(e instanceof Error?e.message:String(e));}}}>查看快照</button></td></tr>)}</tbody></table></div> : <div className="note">尚无北极星版本。建立北极星后，新的母计划草稿会自动绑定当前版本。</div>}
      {selectedVersion && <div className="note" style={{marginTop:12}}><b>北极星 V{selectedVersion.version_no} 快照</b>{fields.map(([key,label])=><div key={key} className="tiny">{label}：{selectedVersion.snapshot[key] || "—"}</div>)}<button className="btn sm" style={{marginTop:8}} onClick={()=>setSelectedVersion(null)}>关闭快照</button></div>}
    </section>
    <section className="panel">
      <div className="panel-head"><div><div className="panel-title">母计划资源基准盘点</div><div className="panel-sub">展示现有来源的真实状态；待审核、缺失或未接入的来源不会标记为已具备。</div></div></div>
      <div className="grid four">{Object.entries(summary).map(([key,value])=><div className="plan-summary-card" key={key}><div className="tiny">{labels[key] ?? key}</div><b>{value}</b></div>)}</div>
      <div className="table-wrap" style={{marginTop:12}}><table><thead><tr><th>类别</th><th>资源</th><th>来源</th><th>状态</th><th>更新时间</th></tr></thead><tbody>{resources.map((item,index)=><tr key={item.source_type+":"+item.source_id+":"+index}><td>{item.category}</td><td>{item.title}</td><td>{displaySourceLabel(item.source_type)}</td><td>{statuses[item.status] ?? item.status}</td><td>{formatBusinessDateTime(item.updated_at)}</td></tr>)}</tbody></table></div>
      {gaps.length>0 && <><div className="note" style={{marginTop:12}}>当前有 {gaps.length} 项资源缺口或待确认事项；缺口登记、素材补充与审核可在素材盘点中维护。</div><div className="table-wrap"><table><thead><tr><th>类别</th><th>缺口</th><th>状态</th><th>优先级</th></tr></thead><tbody>{gaps.map((gap,index)=><tr key={String(gap.id ?? index)}><td>{String(gap.category ?? gap.entity_type ?? "资源")}</td><td>{String(gap.title ?? "待确认")}</td><td>{String(gap.status ?? "open")}</td><td>{String(gap.priority ?? "—")}</td></tr>)}</tbody></table></div><button className="btn sm" style={{marginTop:10}} onClick={onOpenAssets}>前往素材盘点与缺口维护</button></>}
    </section>
  </div>;
}
