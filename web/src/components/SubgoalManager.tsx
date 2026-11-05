import { useEffect, useState } from "react";
import { api, SubGoalRecord, SubPlanVersionRecord } from "../api";
import EmptyHint from "./EmptyHint";

const TYPES = [
  ["quantity","数量目标"],["business","业务目标"],["effect","效果目标"],
  ["quality","质量目标"],["capability","资料/能力建设目标"],
] as const;
const STATUS: Record<string,string> = {
  draft:"草稿",blocked:"依赖阻塞",ready:"可执行",in_progress:"进行中",
  completed:"已完成",failed:"验收未通过",needs_review:"待补充复核",cancelled:"已取消",
};

export default function SubgoalManager({ subPlanId }: { subPlanId: string }) {
  const [versions,setVersions] = useState<SubPlanVersionRecord[]>([]);
  const [draftId,setDraftId] = useState<number | null>(null);
  const [effectiveId,setEffectiveId] = useState<number | null>(null);
  const [draftGoals,setDraftGoals] = useState<SubGoalRecord[]>([]);
  const [effectiveGoals,setEffectiveGoals] = useState<SubGoalRecord[]>([]);
  const [title,setTitle] = useState("");
  const [goalType,setGoalType] = useState<SubGoalRecord["goal_type"]>("business");
  const [criteria,setCriteria] = useState("");
  const [target,setTarget] = useState("1");
  const [description,setDescription] = useState("");
  const [stageId,setStageId] = useState("");
  const [deps,setDeps] = useState<string[]>([]);
  const [parentGoalId,setParentGoalId] = useState("");
  const [priority,setPriority] = useState("3");
  const [goalOrder,setGoalOrder] = useState("1");
  const [editingGoalId,setEditingGoalId] = useState<string | null>(null);
  const [productionGoal,setProductionGoal] = useState<SubGoalRecord | null>(null);
  const [direction,setDirection] = useState("");
  const [suggestedCount,setSuggestedCount] = useState("1");
  const [resources,setResources] = useState("{}");
  const [executionConditions,setExecutionConditions] = useState("");
  const [acceptGoal,setAcceptGoal] = useState<SubGoalRecord | null>(null);
  const [decision,setDecision] = useState("passed");
  const [actualCount,setActualCount] = useState("");
  const [evidence,setEvidence] = useState("{}");
  const [reason,setReason] = useState("");
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");

  async function load() {
    try {
      const detail = await api.subplan(subPlanId);
      const versions = detail.versions ?? [];
      setVersions(versions);
      const draft = versions.find(v => v.status === "draft") ?? null;
      const effective = versions.find(v => v.status === "effective") ?? null;
      setDraftId(draft?.id ?? null);
      setEffectiveId(effective?.id ?? null);
      const [d,e] = await Promise.all([
        draft ? api.subplanGoals(subPlanId,draft.id) : Promise.resolve({items:[]}),
        effective ? api.subplanGoals(subPlanId,effective.id) : Promise.resolve({items:[]}),
      ]);
      setDraftGoals(d.items); setEffectiveGoals(e.items); setError("");
    } catch(e) { setError(e instanceof Error ? e.message : "读取目标失败"); }
  }
  useEffect(() => { void load(); }, [subPlanId]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await action(); await load(); }
    catch(e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function createGoal() {
    if (!draftId) return;
    await run(() => api.createSubplanGoal(subPlanId,{
      version_id:draftId, title, goal_type:goalType, acceptance_criteria:criteria,
      description, target_count:goalType==="quantity" ? Number(target) : null,
      stage_id:stageId ? Number(stageId) : null, dependencies:deps,
      parent_goal_id:parentGoalId || null, goal_order:Number(goalOrder), priority:Number(priority),
      requires_production:false,
    }));
    setTitle(""); setCriteria(""); setDescription(""); setDeps([]); setParentGoalId("");
    setEditingGoalId(null); setPriority("3"); setGoalOrder(String(draftGoals.length+2));
  }
  function editGoal(goal: SubGoalRecord) {
    setEditingGoalId(goal.id); setTitle(goal.title); setGoalType(goal.goal_type);
    setCriteria(goal.acceptance_criteria); setDescription(goal.description);
    setTarget(String(goal.target_count ?? 1)); setStageId(goal.stage_id ? String(goal.stage_id) : "");
    setDeps(goal.dependencies); setParentGoalId(goal.parent_goal_id ?? "");
    setPriority(String(goal.priority)); setGoalOrder(String(goal.goal_order));
  }
  async function saveGoal() {
    if (!editingGoalId) return;
    await run(() => api.updateSubplanGoal(subPlanId,editingGoalId!,{
      title,goal_type:goalType,acceptance_criteria:criteria,description,
      target_count:goalType==="quantity" ? Number(target) : null,
      stage_id:stageId ? Number(stageId) : null,dependencies:deps,
      parent_goal_id:parentGoalId || null,priority:Number(priority),goal_order:Number(goalOrder),
    }));
    setEditingGoalId(null); setTitle(""); setCriteria(""); setDescription("");
  }
  async function saveProductionPlan() {
    if (!productionGoal) return;
    let resourceRequirements: Record<string,unknown>;
    try { resourceRequirements=JSON.parse(resources); }
    catch { setError("资源需求必须是有效 JSON 对象"); return; }
    await run(() => api.saveSubgoalProductionPlan(subPlanId,productionGoal.id,{
      content_direction:direction,suggested_count:Number(suggestedCount),
      resource_requirements:resourceRequirements,priority:productionGoal.priority,
      execution_conditions:executionConditions,acceptance_relation:productionGoal.acceptance_criteria,
    }));
    setProductionGoal(null);
  }
  async function submitAcceptance() {
    if (!acceptGoal) return;
    let proof: Record<string,unknown>;
    try { proof=JSON.parse(evidence); }
    catch { setError("验收证据必须是有效 JSON 对象"); return; }
    await run(() => api.acceptSubplanGoal(subPlanId,acceptGoal.id,{
      decision,reason,evidence:proof,
      actual_count:acceptGoal.goal_type==="quantity" ? Number(actualCount) : undefined,
    }));
    setAcceptGoal(null);
  }
  const stageOptions = versions.find(v=>v.id===draftId)?.stages ?? [];
  return <section className="panel">
    <div className="panel-head"><div><div className="panel-title">子目标与生产计划</div><div className="panel-sub">目标独立于策略阶段；依赖、验收证据和生产计划绑定具体子计划版本。</div></div><button className="btn sm" onClick={()=>void load()}>刷新</button></div>
    {error && <div className="note" role="alert">{error}</div>}
    {!draftId && <div className="note">当前没有子计划草稿。请先在「阶段管理」中保存一次子计划变更，生成新版本草稿，再创建或维护目标。</div>}
    {draftId && <><div className="note">编辑版本 #{draftId}（草稿）。新版本中的目标独立创建，不会继承旧版本完成状态。</div>
      <div className="grid two">
        <div><label className="form-label">目标名称</label><input className="field" value={title} onChange={e=>setTitle(e.target.value)} />
          <label className="form-label">目标类型</label><select className="field" value={goalType} onChange={e=>setGoalType(e.target.value as SubGoalRecord["goal_type"])}>{TYPES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>
          {goalType==="quantity" && <><label className="form-label">目标数量</label><input className="field" type="number" min="0" value={target} onChange={e=>setTarget(e.target.value)} /></>}
          <label className="form-label">关联策略阶段（可选）</label><select className="field" value={stageId} onChange={e=>setStageId(e.target.value)}><option value="">不关联阶段</option>{stageOptions.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select>
          <label className="form-label">父目标</label><select className="field" value={parentGoalId} onChange={e=>setParentGoalId(e.target.value)}><option value="">顶层目标</option>{draftGoals.filter(g=>g.id!==editingGoalId).map(g=><option key={g.id} value={g.id}>{g.title}</option>)}</select>
          <div className="grid two"><div><label className="form-label">优先级（1-5）</label><input className="field" type="number" min="1" max="5" value={priority} onChange={e=>setPriority(e.target.value)}/></div><div><label className="form-label">排序</label><input className="field" type="number" min="1" value={goalOrder} onChange={e=>setGoalOrder(e.target.value)}/></div></div>
        </div>
        <div><label className="form-label">目标说明</label><textarea className="field" rows={3} value={description} onChange={e=>setDescription(e.target.value)} />
          <label className="form-label">验收条件（必填）</label><textarea className="field" rows={3} value={criteria} onChange={e=>setCriteria(e.target.value)} />
          <label className="form-label">前置依赖</label><select className="field" multiple value={deps} onChange={e=>setDeps(Array.from(e.target.selectedOptions,o=>o.value))}>{draftGoals.map(g=><option key={g.id} value={g.id}>{g.title}</option>)}</select>
        </div>
      </div>
      <div className="plan-form-actions"><button className="btn primary" disabled={busy || !title.trim() || !criteria.trim()} onClick={()=>void (editingGoalId ? saveGoal() : createGoal())}>{editingGoalId ? "保存目标修改" : "创建子目标"}</button>{editingGoalId && <button className="btn" onClick={()=>{setEditingGoalId(null);setTitle("");setCriteria("");setDescription("");}}>取消编辑</button>}</div>
      {draftGoals.length===0 ? <EmptyHint next="填写目标名称和验收条件">当前草稿版本尚无子目标</EmptyHint> : <div className="plan-stage-list">{draftGoals.map(g=><article className="plan-stage-card" key={g.id} style={{marginLeft:g.parent_goal_id ? 18 : 0}}>
        <div className="panel-head"><b>{g.parent_goal_id ? "↳ " : ""}{g.title}</b><span className="tag gray">{STATUS[g.status]??g.status}</span></div>
        <div className="tiny">{TYPES.find(x=>x[0]===g.goal_type)?.[1]} · 优先级 {g.priority} · {g.acceptance_criteria}</div>
        {g.dependencies.length>0 && <div className="tiny">依赖：{g.dependencies.map(id=>{const dep=draftGoals.find(x=>x.id===id);return (dep?.title??id)+"（"+(STATUS[dep?.status??""]??"未知状态")+"）";}).join("、")}</div>}
        <div className="plan-form-actions"><button className="btn sm" onClick={()=>editGoal(g)}>编辑目标</button><button className="btn sm" disabled={g.stage_id===null} onClick={()=>{setProductionGoal(g);setDirection(g.production_plan?.content_direction ?? g.description);setSuggestedCount(String(g.production_plan?.suggested_count ?? g.target_count ?? 1));setResources(JSON.stringify(g.production_plan?.resource_requirements ?? {},null,2));setExecutionConditions(g.production_plan?.execution_conditions ?? "");}}> {g.production_plan ? "编辑生产计划" : "配置子目标生产计划"}</button></div>
        {g.stage_id===null && <div className="tiny">需要先关联策略阶段，才能配置内容生产计划。</div>}
        {g.production_plan && <div className="note">生产方向：{g.production_plan.content_direction} · 建议数量：{g.production_plan.suggested_count} · 执行条件：{g.production_plan.execution_conditions}</div>}
        {productionGoal?.id===g.id && <div className="plan-stage-editor"><label className="form-label">内容方向</label><textarea className="field" rows={2} value={direction} onChange={e=>setDirection(e.target.value)}/><label className="form-label">建议数量</label><input className="field" type="number" min="1" value={suggestedCount} onChange={e=>setSuggestedCount(e.target.value)}/><label className="form-label">资源需求 JSON</label><textarea className="field" rows={2} value={resources} onChange={e=>setResources(e.target.value)}/><label className="form-label">执行条件</label><textarea className="field" rows={2} value={executionConditions} onChange={e=>setExecutionConditions(e.target.value)}/><button className="btn primary" disabled={busy} onClick={()=>void saveProductionPlan()}>保存生产计划</button></div>}
      </article>)}</div>}
    </>}
    {effectiveId && <><div className="panel-head" style={{marginTop:20}}><div><div className="panel-title">生效版本验收</div><div className="panel-sub">验收只记录服务端状态变更和不可变证据；客户端不能直接提交 completed 状态。</div></div><span className="tag gray">版本 #{effectiveId}</span></div>
      {effectiveGoals.length===0 ? <EmptyHint next="在子计划草稿版本创建目标并审核生效">当前生效版本没有子目标</EmptyHint> : <div className="plan-stage-list">{effectiveGoals.map(g=><article className="plan-stage-card" key={g.id}>
        <div className="panel-head"><b>{g.title}</b><span className="tag gray">{STATUS[g.status]??g.status}</span></div><div className="tiny">{TYPES.find(x=>x[0]===g.goal_type)?.[1]} · 验收条件：{g.acceptance_criteria}</div>
        {g.dependencies.length>0 && <div className="tiny">前置目标：{g.dependencies.map(id=>{const dep=effectiveGoals.find(x=>x.id===id);return (dep?.title??id)+"（"+(STATUS[dep?.status??""]??"未知状态")+"）";}).join("、")}</div>}
        {g.production_plan && <div className="note">生产方向：{g.production_plan.content_direction} · 建议数量：{g.production_plan.suggested_count} · 执行条件：{g.production_plan.execution_conditions}</div>}
        {g.stage_id!==null && <button className="btn sm" onClick={()=>{setProductionGoal(g);setDirection(g.production_plan?.content_direction ?? g.description);setSuggestedCount(String(g.production_plan?.suggested_count ?? g.target_count ?? 1));setResources(JSON.stringify(g.production_plan?.resource_requirements ?? {},null,2));setExecutionConditions(g.production_plan?.execution_conditions ?? "");}}> {g.production_plan ? "调整生产计划" : "配置生产计划"}</button>}
        {productionGoal?.id===g.id && <div className="plan-stage-editor"><label className="form-label">内容方向</label><textarea className="field" rows={2} value={direction} onChange={e=>setDirection(e.target.value)}/><label className="form-label">建议数量</label><input className="field" type="number" min="1" value={suggestedCount} onChange={e=>setSuggestedCount(e.target.value)}/><label className="form-label">资源需求 JSON</label><textarea className="field" rows={2} value={resources} onChange={e=>setResources(e.target.value)}/><label className="form-label">执行条件</label><textarea className="field" rows={2} value={executionConditions} onChange={e=>setExecutionConditions(e.target.value)}/><button className="btn primary" disabled={busy} onClick={()=>void saveProductionPlan()}>保存生产计划</button></div>}
                {g.latest_evidence && <div className="note">最近验收：{g.latest_evidence.decision} · {g.latest_evidence.reason} · 证据：{JSON.stringify(g.latest_evidence.evidence)}</div>}
        {(g.evidence_history?.length ?? 0)>1 && <div className="tiny">历史验收记录：{g.evidence_history!.length} 条，最近记录之外的证据仍保留在历史中。</div>}
        {g.status!=="completed" && <button className="btn sm" onClick={()=>{setAcceptGoal(g);setDecision("passed");setReason("");setEvidence("{}");setActualCount(String(g.target_count??""));}}>提交验收</button>}
        {acceptGoal?.id===g.id && <div className="plan-stage-editor"><label className="form-label">验收结论</label><select className="field" value={decision} onChange={e=>setDecision(e.target.value)}><option value="passed">通过</option><option value="failed">未通过</option><option value="needs_review">待补充复核</option></select>{g.goal_type==="quantity" && <><label className="form-label">实际数量</label><input className="field" type="number" min="0" value={actualCount} onChange={e=>setActualCount(e.target.value)}/></>}<label className="form-label">原因</label><textarea className="field" rows={2} value={reason} onChange={e=>setReason(e.target.value)}/><label className="form-label">证据 JSON</label><textarea className="field" rows={3} value={evidence} onChange={e=>setEvidence(e.target.value)}/><button className="btn primary" disabled={busy} onClick={()=>void submitAcceptance()}>提交验收记录</button></div>}
      </article>)}</div>}
    </>}
  </section>;
}
