import { useEffect, useState } from "react";
import { useApp } from "../AppContext";
import { api, Goal, MasterPlan, PlanResponse, PlanSignatureHistory, SpiralAnalysis, SubPlanStageRecord } from "../api";
import { ALL_PLATFORMS, Platform, platformMeta } from "../platforms";
import EmptyHint from "../components/EmptyHint";

type Tab = "master" | "platforms" | "stages";
type StageStatus = "pending" | "active" | "completed" | "paused" | "cancelled";
type Stage = { id: string; name: string; direction: string; count: number; start: string; end: string; rule: string; status: StageStatus; confirmed: number };
type Child = { id: string; platform: Platform; name: string; direction: string; count: number; status: "draft" | "review" | "effective"; signature: string; versionId: number; versionNo: number; stageRecords: SubPlanStageRecord[] };
const stageLabels: Record<StageStatus, string> = { pending: "待执行", active: "执行中", completed: "已完成", paused: "已暂停", cancelled: "已取消" };
const childLabels = { draft: "草稿", review: "待审核", effective: "已生效" };
const localDate = (date: Date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
};
const today = () => localDate(new Date());
const dateAfterDays = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return localDate(date);
};
function snapshot(item: PlanSignatureHistory): MasterPlan | null {
  try { return (typeof item.plan_snapshot === "string" ? JSON.parse(item.plan_snapshot) : item.plan_snapshot) as MasterPlan; }
  catch { return null; }
}

export default function PlanCenter() {
  const { showToast } = useApp();
  const [tab, setTab] = useState<Tab>("master");
  const [data, setData] = useState<PlanResponse | null>(null);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [review, setReview] = useState<SpiralAnalysis | null>(null);
  const [adoption, setAdoption] = useState<{ adopted_dimensions: string[]; drafts: Array<{ sub_plan_id: string; platform: string; draft_version: { version_no: number } }>; message: string } | null>(null);
  const [showStageHistory, setShowStageHistory] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<MasterPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [version, setVersion] = useState<PlanSignatureHistory | null>(null);
  const [children, setChildren] = useState<Child[]>([]);
  const [selectedChildId, setSelectedChildId] = useState("");
  const [masterVersionId, setMasterVersionId] = useState<number | null>(null);
  const [masterDraftVersionId, setMasterDraftVersionId] = useState<number | null>(null);
  const [childForm, setChildForm] = useState(false);
  const [childPlatform, setChildPlatform] = useState<Platform>("xhs");
  const [childName, setChildName] = useState("");
  const [childDirection, setChildDirection] = useState("");
  const [childCount, setChildCount] = useState(5);
  const [editingChild, setEditingChild] = useState<Child | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [stageForm, setStageForm] = useState(false);
  const [stageName, setStageName] = useState("");
  const [stageDirection, setStageDirection] = useState("");
  const [stageCount, setStageCount] = useState(10);
  const [stageStart, setStageStart] = useState(today());
  const [stageEnd, setStageEnd] = useState(dateAfterDays(30));
  const [stageRule, setStageRule] = useState("达到计划篇数，且所有内容完成审核");
  const [editingStage, setEditingStage] = useState<Stage | null>(null);

  const load = async (preferredChildId?: string) => {
    setLoading(true);
    try {
      const [planResult, goalResult, versionResult, subplanResult] = await Promise.all([
        api.plans(), api.goals(), api.planVersions(), api.subplans({ page: 1, pageSize: 100 }),
      ]);
      const effectiveMaster = versionResult.items.find((item) => item.status === "effective");
      const pendingMaster = versionResult.items.find((item) => item.status === "draft");
      setMasterDraftVersionId(pendingMaster?.id ?? null);
      setData(planResult); setGoals(goalResult); setDraft(structuredClone(planResult.plan));
      setMasterVersionId(effectiveMaster?.id ?? null);
      const details = await Promise.all(subplanResult.items.map((item) => api.subplan(item.id)));
      const mapped: Child[] = details.map((detail) => {
        const identity = detail.subplan;
        // The identity pointer remains on the effective version until review;
        // surface the newest draft first so edits can be reviewed rather than hidden.
        const current = detail.versions.find((v) => v.status === "draft")
          ?? detail.versions.find((v) => v.id === identity.current_version_id)
          ?? detail.versions[0];
        const snapshot = current?.snapshot ?? {};
        const stageRecords = current?.stages ?? [];
        const status = identity.status === "active" && current?.status === "effective"
          ? "effective" : current?.status === "draft" ? "draft" : "review";
        return {
          id: identity.id, platform: identity.platform,
          name: String(snapshot.title ?? identity.title),
          direction: String(snapshot.direction ?? ""),
          count: stageRecords.reduce((sum, stage) => sum + Number(stage.target_count || 0), 0),
          status, signature: planResult.signature,
          versionId: Number(current?.id ?? identity.current_version_id ?? 0),
          versionNo: Number(current?.version_no ?? identity.current_version_no ?? 1),
          stageRecords,
        };
      });
      setChildren(mapped);
      setSelectedChildId((current) => mapped.some((item) => item.id === current) ? current : mapped[0]?.id ?? "");
      const preferred = preferredChildId ?? selectedChildId;
      const selectedId = mapped.some((v) => v.id === preferred) ? preferred : mapped[0]?.id ?? "";
      setSelectedChildId(selectedId);
      const selected = mapped.find((item) => item.id === selectedId);
      setStages((selected?.stageRecords ?? []).map((stage) => ({
        id: String(stage.id), name: stage.title, direction: stage.direction ?? "",
        count: Number(stage.target_count), start: stage.start_date ?? "", end: stage.end_date ?? "",
        rule: stage.completion_condition ?? "",
        status: stage.status === "in_progress" ? "active" : stage.status,
        confirmed: Number(stage.confirmed_published_count ?? 0),
      })));
      try { setReview(await api.gaps()); } catch { setReview(null); }
      setDirty(false); setSaved(false); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "计划数据加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const changeDraft = (value: MasterPlan) => { setDraft(value); setDirty(true); setSaved(false); };
  const saveDraft = async () => {
    if (!draft) return;
    try {
      const result = await api.createPlanDraft(draft);
      setMasterDraftVersionId(result.draft.id);
      setSaved(true); setDirty(false);
      showToast(`母计划草稿 v${result.draft.version_no} 已写入服务器，尚未生效`, "success");
    } catch (e) { showToast("保存母计划草稿失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const submitMasterReview = async () => {
    if (!masterDraftVersionId) { showToast("请先保存一个母计划草稿版本", "error"); return; }
    if (!window.confirm("确认审核通过当前母计划草稿？这会切换生效母计划，并暂停基于旧版本的子计划。")) return;
    try {
      await api.reviewMasterPlan(masterDraftVersionId, "approve");
      showToast("母计划已审核生效", "success");
      await load();
    } catch (e) { showToast("母计划审核失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const resetDraft = () => { if (data) { setDraft(structuredClone(data.plan)); setDirty(false); setSaved(false); } };
  const adoptGap = async (dimension: string) => {
    try {
      const result = await api.adoptGaps([dimension]);
      setAdoption(result);
      showToast("已生成平台子计划修订草稿，需逐项审核后生效", "success");
      await load();
    } catch (e) { showToast("采纳失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const createChild = async () => {
    if (!childName.trim()) { showToast("请填写子计划名称", "error"); return; }
    if (!masterVersionId) { showToast("没有可用的生效主计划版本，请先审核主计划", "error"); return; }
    try {
      const created = await api.createSubplan({
        master_plan_version_id: masterVersionId, platform: childPlatform, title: childName.trim(),
        snapshot: { title: childName.trim(), platform: childPlatform, direction: childDirection },
        stages: [{
          title: "初始阶段", direction: childDirection, target_count: Math.max(1, childCount),
          start_date: today(), end_date: dateAfterDays(30),
          completion_condition: "达到计划篇数，且所有内容完成审核",
        }],
      });
      setChildForm(false); setChildName(""); setChildDirection("");
      setSelectedChildId(created.subplan.id);
      showToast("子计划草稿已写入服务器，尚未获得生产授权", "success");
      await load();
    } catch (e) { showToast("创建失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const saveChild = async () => {
    if (!editingChild) return;
    const existing = children.find((item) => item.id === editingChild.id);
    if (!existing) return;
    try {
      await api.updateSubplan(editingChild.id, {
        based_on_version_id: existing.versionId, expected_version_no: existing.versionNo,
        snapshot: { title: editingChild.name, platform: editingChild.platform, direction: editingChild.direction },
        stages: existing.stageRecords.map((stage) => ({
          stage_key: stage.stage_key, status: stage.status,
          title: stage.title, direction: stage.direction, target_count: stage.target_count,
          start_date: stage.start_date, end_date: stage.end_date,
          completion_condition: stage.completion_condition, notes: stage.notes,
        })),
        change_note: "计划中心编辑子计划",
      });
      setEditingChild(null); showToast("已创建新的子计划草稿版本，需重新审核", "success");
      await load();
    } catch (e) { showToast("保存失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const reviewChild = async (item: Child) => {
    if (item.status === "effective") return;
    if (!window.confirm("确认审核通过该子计划版本？通过后该平台子计划将获得生产授权。")) return;
    try {
      await api.reviewSubplan(item.id, item.versionId, "approve");
      showToast("子计划已审核生效，生产授权已按服务端规则启用", "success");
      await load();
    } catch (e) { showToast("审核失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const createStage = async () => {
    if (!stageName.trim()) { showToast("请填写阶段名称", "error"); return; }
    if (stageEnd < stageStart) { showToast("结束日期不能早于开始日期", "error"); return; }
    const child = children.find((item) => item.id === selectedChildId);
    if (!child) { showToast("请先选择一个平台子计划", "error"); return; }
    try {
      await api.addSubplanStage(child.id, {
        based_on_version_id: child.versionId, expected_version_no: child.versionNo,
        stage: {
          title: stageName.trim(), direction: stageDirection, target_count: Math.max(1, stageCount),
          start_date: stageStart, end_date: stageEnd, completion_condition: stageRule,
        },
      });
      setStageForm(false); setStageName(""); setStageDirection("");
      showToast("阶段已写入新的子计划草稿版本，需重新审核", "success");
      await load();
    } catch (e) { showToast("创建阶段失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const saveStage = async () => {
    if (!editingStage) return;
    const child = children.find((item) => item.id === selectedChildId);
    if (!child) return;
    try {
      await api.editSubplanStage(child.id, Number(editingStage.id), {
        based_on_version_id: child.versionId, expected_version_no: child.versionNo,
        stage: {
          title: editingStage.name, direction: editingStage.direction,
          target_count: editingStage.count, start_date: editingStage.start || null,
          end_date: editingStage.end || null, completion_condition: editingStage.rule,
        },
      });
      setEditingStage(null); showToast("阶段修改已保存为新草稿版本，需重新审核", "success");
      await load();
    } catch (e) { showToast("保存阶段失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const deleteStage = async (stage: Stage) => {
    const child = children.find((item) => item.id === selectedChildId);
    if (!child || !window.confirm("确认删除阶段“" + stage.name + "”？此操作会创建新的子计划草稿版本。")) return;
    try {
      await api.deleteSubplanStage(child.id, Number(stage.id), child.versionId, child.versionNo);
      showToast("阶段已从新草稿版本移除，需重新审核", "success");
      await load();
    } catch (e) { showToast("删除阶段失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const platforms = (data?.plan.enabled_platforms ?? []).filter((p): p is Platform => ALL_PLATFORMS.includes(p as Platform));

  return <div className="page plan-center-page">
    <div className="page-head"><div><div className="eyebrow">内容生产 · 计划体系</div><h1 className="page-title">计划管理</h1><div className="page-desc">主计划确定目标与策略，平台子计划承接平台差异，阶段负责节奏与完成条件，生产轮次按已生效计划执行。</div></div><div className="head-actions"><button className="btn" onClick={() => void load()} disabled={loading}>↻ 刷新</button><button className="btn primary" disabled={!data} onClick={() => { if (data) { setDraft(structuredClone(data.plan)); setDirty(true); setSaved(false); setTab("master"); } }}>＋ 新建版本草稿</button></div></div>
    <div className="plan-hierarchy"><div className="plan-hierarchy-node active"><b>主计划</b><small>目标 · 策略 · 版本</small></div><span>→</span><div className={"plan-hierarchy-node " + (tab === "platforms" ? "active" : "")}><b>平台子计划</b><small>平台方向 · 产量 · 授权</small></div><span>→</span><div className={"plan-hierarchy-node " + (tab === "stages" ? "active" : "")}><b>阶段</b><small>时间 · 目标 · 完成条件</small></div><span>→</span><div className="plan-hierarchy-node"><b>生产轮次</b><small>生产线 · 日历 · 内容</small></div></div>
    <div className="tabs page-tabs"><button className={"tab " + (tab === "master" ? "active" : "")} onClick={() => setTab("master")}>主计划与版本</button><button className={"tab " + (tab === "platforms" ? "active" : "")} onClick={() => setTab("platforms")}>平台子计划</button><button className={"tab " + (tab === "stages" ? "active" : "")} onClick={() => setTab("stages")}>阶段管理</button></div>
    {loading && <div className="panel"><div className="loading"><div className="spinner" />正在读取计划…</div></div>}
    {error && <div className="panel"><div className="note" role="alert" style={{ color: "var(--red)" }}>{error}</div><button className="btn sm" onClick={() => void load()}>重试</button></div>}

    {!loading && !error && tab === "master" && data && draft && <>
      <div className="panel">
        <div className="panel-head"><div><div className="panel-title">当前主计划</div><div className="panel-sub">现有 API 只维护一份当前母计划；历史签名作为版本记录，不代表多个独立主计划。</div></div><span className={"tag " + (data.source === "plan_settings" ? "green" : "gray")}>{data.source === "plan_settings" ? "服务器已保存" : "由最近轮次推导"}</span></div>
        <div className="grid two"><div className="plan-summary-card"><div className="tiny">当前版本签名</div><div className="plan-signature">{data.signature}</div><div className="tiny">最后更新：{data.updated_at ?? "尚未保存"}</div></div><div className="plan-summary-card"><div className="tiny">目标与范围</div><b>{goals.find((g) => g.id === data.plan.cadence?.goal_id)?.goal_text ?? "尚未关联目标"}</b><div className="tiny">启用平台 {platforms.length} 个 · 每平台默认 {data.plan.topic_count_per_platform} 篇/轮</div></div></div>
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="panel-head"><div><div className="panel-title">复盘建议与版本关系</div><div className="panel-sub">仅用户可采纳的覆盖缺口可以写回母计划；采纳后会生成新签名，并标记基于旧版本的活跃轮次。</div></div><button className="btn sm" onClick={() => { window.location.search = "?page=gaps"; }}>查看完整复盘</button></div>
          {adoption && <div className="note" style={{ marginBottom: 10 }}>最近采纳：{adoption.adopted_dimensions.join("、")} → 已创建 {adoption.drafts.length} 个平台子计划草稿版本；需逐项审核</div>}
          {(review?.gaps ?? []).filter((g) => g.audience === "user" && g.gap_type === "coverage_gap").length === 0 ? <EmptyHint next="完成一轮内容生产和数据采集后，复盘系统会给出可采纳建议">当前没有可采纳的覆盖缺口建议</EmptyHint> : <div className="list">{(review?.gaps ?? []).filter((g) => g.audience === "user" && g.gap_type === "coverage_gap").map((g) => <div className="task-row" key={g.dimension}><div className="task-main"><div className="task-title">{g.dimension}</div><div className="task-desc">{g.suggestion_text} · 当前 {g.current_count} 条 · 建议 {g.target_count ?? "—"} 条 · 样本 {g.sample_count}</div></div><button className="btn sm primary" onClick={() => void adoptGap(g.dimension)}>采纳并生成新版本</button></div>)}</div>}
        </div>
        <div className="panel-head" style={{ marginTop: 16 }}><div><div className="panel-title">主计划草稿编辑</div><div className="panel-sub">保存会创建服务端不可变草稿版本；只有审核通过后才切换生效计划。</div></div><span className={"tag " + (dirty ? "orange" : saved ? "green" : "gray")}>{dirty ? "有未保存修改" : saved ? "草稿已暂存" : "与当前版本一致"}</span></div>
        <div className="grid two"><div><label className="form-label">每个平台默认选题数</label><input className="field" type="number" min={1} max={50} value={draft.topic_count_per_platform} onChange={(e) => changeDraft({ ...draft, topic_count_per_platform: Math.max(1, Number(e.target.value) || 1) })}/><label className="form-label" style={{ marginTop: 12 }}>关联目标</label><select className="field" value={draft.cadence?.goal_id ?? ""} onChange={(e) => changeDraft({ ...draft, cadence: { ...draft.cadence, goal_id: e.target.value || undefined } })}><option value="">暂不关联</option>{goals.map((g) => <option key={g.id} value={g.id}>{g.goal_text}</option>)}</select><label className="form-label" style={{ marginTop: 12 }}>内容形式</label><select className="field" value={draft.content_format} onChange={(e) => changeDraft({ ...draft, content_format: e.target.value })}><option value="image_text">图文</option><option value="video">视频</option><option value="mixed">混合</option></select></div><div><label className="form-label">启用平台</label><div className="plan-platform-toggles">{ALL_PLATFORMS.map((p) => <label className="plan-platform-toggle" key={p}><input type="checkbox" checked={draft.enabled_platforms.includes(p)} onChange={(e) => changeDraft({ ...draft, enabled_platforms: e.target.checked ? [...draft.enabled_platforms, p] : draft.enabled_platforms.filter((v) => v !== p) })}/><span>{platformMeta(p).label}</span></label>)}</div><label className="form-label" style={{ marginTop: 12 }}>选题方向配额</label>{Object.entries(draft.dimension_allocation).map(([name, count]) => <div className="plan-dimension-row" key={name}><span>{name}</span><input className="field" type="number" min={0} value={count} onChange={(e) => changeDraft({ ...draft, dimension_allocation: { ...draft.dimension_allocation, [name]: Math.max(0, Number(e.target.value) || 0) } })}/><button className="btn sm" onClick={() => { const next = { ...draft.dimension_allocation }; delete next[name]; changeDraft({ ...draft, dimension_allocation: next }); }}>移除</button></div>)}<button className="btn sm" style={{ marginTop: 8 }} onClick={() => { const name = window.prompt("输入选题方向名称"); if (name?.trim()) changeDraft({ ...draft, dimension_allocation: { ...draft.dimension_allocation, [name.trim()]: 1 } }); }}>＋ 添加方向</button></div></div>
        <div className="plan-form-actions"><button className="btn" onClick={resetDraft} disabled={!dirty && !saved}>放弃修改</button><button className="btn primary" onClick={saveDraft} disabled={!dirty}>保存草稿</button><button className="btn" onClick={() => void submitMasterReview()} disabled={!masterDraftVersionId}>审核并生效</button></div>
        {data.stale_rounds.length > 0 && <div className="note" style={{ marginTop: 12, color: "var(--orange)" }}>{data.stale_rounds.length} 个活跃轮次仍基于旧计划，可到内容生产 → 轮次状态查看。</div>}
      </div>
      <div className="panel"><div className="panel-head"><div><div className="panel-title">版本历史</div><div className="panel-sub">来自服务端 signature_history；可查看历史计划快照。</div></div><span className="tag gray">{data.history.length} 条记录</span></div>{data.history.length === 0 ? <EmptyHint next="保存母计划后，系统会记录计划签名">暂无版本历史</EmptyHint> : <div className="table-wrap"><table><thead><tr><th>版本</th><th>关联范围</th><th>来源</th><th>记录时间</th><th>操作</th></tr></thead><tbody>{data.history.map((h, i) => <tr key={h.plan_signature + i}><td><code>{h.plan_signature.slice(0, 14)}…</code></td><td>{h.round_number != null ? "第 " + h.round_number + " 轮" : h.round_id != null ? "历史轮次" : "主计划"}</td><td>{h.author ?? "系统"}</td><td>{h.created_at}</td><td><button className="btn sm" onClick={() => setVersion(h)}>查看快照</button></td></tr>)}</tbody></table></div>}{version && <div className="plan-version-detail"><div className="panel-head"><b>版本快照 · {version.plan_signature.slice(0, 14)}…</b><button className="btn sm" onClick={() => setVersion(null)}>关闭</button></div>{snapshot(version) ? <div className="plan-version-grid"><span>默认篇数：{snapshot(version)?.topic_count_per_platform}</span><span>启用平台：{snapshot(version)?.enabled_platforms.map((p) => platformMeta(p as Platform).label).join("、")}</span><span>内容形式：{snapshot(version)?.content_format}</span><span>方向配额：{Object.entries(snapshot(version)?.dimension_allocation ?? {}).map(([k,v]) => k + " " + v).join("、") || "未设置"}</span></div> : <div className="note">历史快照无法解析。</div>}</div>}</div>
    </>}

    {!loading && !error && tab === "platforms" && data && <>
      <div className="panel"><div className="panel-head"><div><div className="panel-title">平台子计划</div><div className="panel-sub">各平台独立维护方向、目标篇数与执行授权，并引用来源主计划版本。</div></div><div className="head-actions"><button className="btn" onClick={async () => { if (!masterVersionId) { showToast("请先审核生效母计划", "error"); return; } try { const result = await api.aiDraftSubplans(masterVersionId, platforms); showToast(`AI 已生成 ${result.items.length} 个子计划草稿，需人工审核`, "success"); await load(); } catch (e) { showToast("AI 拆解失败：" + (e instanceof Error ? e.message : String(e)), "error"); } }}>AI 拆解</button><button className="btn primary" onClick={() => { setChildForm(true); setChildPlatform(platforms[0] ?? "xhs"); setChildCount(data.plan.topic_count_per_platform); }}>＋ 手工创建</button></div></div><div className="note">来源主计划版本：<code>{data.signature.slice(0, 16)}…</code>。子计划和版本均持久化在当前账号组数据库；草稿不会自动获得生产授权。</div><div className="grid two" style={{ marginTop: 12 }}>{platforms.map((p) => { const child = children.find((c) => c.platform === p); return <div className="plan-child-card" key={p}><div className="panel-head"><b>{platformMeta(p).label}</b><span className="tag gray">{child ? childLabels[child.status] : "尚未创建子计划"}</span></div>{child ? <><h3>{child.name}</h3><p>{child.direction || "未填写方向"}</p><div className="tiny">目标 {child.count} 篇 · 来源版本 {child.signature.slice(0, 10)}…</div><div className="plan-form-actions"><button className="btn sm" onClick={() => setEditingChild({ ...child })}>编辑</button><button className="btn sm" onClick={() => reviewChild(child)}>{child.status === "draft" ? "提交审核" : child.status === "review" ? "审核并生效" : "已生效"}</button></div></> : <><p>继承主计划目标与约束；当前没有独立子计划记录。</p><button className="btn sm" onClick={() => { setChildForm(true); setChildPlatform(p); setChildCount(data.plan.topic_count_per_platform); }}>创建该平台子计划</button></>}</div>; })}</div></div>
      {childForm && <div className="panel"><div className="panel-head"><b>手工创建子计划</b><button className="btn sm" onClick={() => setChildForm(false)}>取消</button></div><div className="grid two"><div><label className="form-label">平台</label><select className="field" value={childPlatform} onChange={(e) => setChildPlatform(e.target.value as Platform)}>{ALL_PLATFORMS.map((p) => <option value={p} key={p}>{platformMeta(p).label}</option>)}</select><label className="form-label" style={{ marginTop: 10 }}>名称</label><input className="field" value={childName} onChange={(e) => setChildName(e.target.value)}/></div><div><label className="form-label">选题方向</label><textarea className="field" rows={3} value={childDirection} onChange={(e) => setChildDirection(e.target.value)}/><label className="form-label" style={{ marginTop: 10 }}>目标篇数</label><input className="field" type="number" min={1} value={childCount} onChange={(e) => setChildCount(Math.max(1, Number(e.target.value) || 1))}/></div></div><div className="plan-form-actions"><button className="btn primary" onClick={createChild}>保存子计划草稿</button></div></div>}
      {editingChild && <div className="panel"><div className="panel-head"><b>编辑子计划 · {platformMeta(editingChild.platform).label}</b><button className="btn sm" onClick={() => setEditingChild(null)}>取消</button></div><label className="form-label">名称</label><input className="field" value={editingChild.name} onChange={(e) => setEditingChild({ ...editingChild, name: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>方向</label><textarea className="field" rows={3} value={editingChild.direction} onChange={(e) => setEditingChild({ ...editingChild, direction: e.target.value })}/><div className="note">子计划总目标篇数由各阶段目标合计；请在「阶段管理」中调整阶段篇数。</div><div className="plan-form-actions"><button className="btn primary" onClick={saveChild}>保存修改</button></div></div>}
    </>}

    {!loading && !error && tab === "stages" && <div className="panel"><div className="panel-head"><div><div className="panel-title">阶段管理</div><div className="panel-sub">按阶段管理方向、目标篇数、时间和完成条件；不使用复杂步骤或依赖图。</div></div><div className="head-actions"><button className="btn" onClick={() => setShowStageHistory((v) => !v)}>{showStageHistory ? "返回当前阶段" : "查看历史阶段"}</button><button className="btn primary" onClick={() => setStageForm(true)}>＋ 新建阶段</button></div></div><div className="note">阶段变更会创建不可变子计划草稿版本；需要重新审核后才生效。已确认发布数来自阶段发布统计。</div><div className="form-row"><label className="form-label" htmlFor="stage-subplan-select">所属平台子计划</label><select id="stage-subplan-select" className="field" value={selectedChildId} onChange={(e) => { setSelectedChildId(e.target.value); const child = children.find((item) => item.id === e.target.value); setStages((child?.stageRecords ?? []).map((stage) => ({ id: String(stage.id), name: stage.title, direction: stage.direction ?? "", count: Number(stage.target_count), start: stage.start_date ?? "", end: stage.end_date ?? "", rule: stage.completion_condition ?? "", status: stage.status === "in_progress" ? "active" : stage.status, confirmed: Number(stage.confirmed_published_count ?? 0) }))); setShowStageHistory(false); }}><option value="">请选择子计划</option>{children.map((child) => <option key={child.id} value={child.id}>{platformMeta(child.platform).label} · {child.name}</option>)}</select></div>{stages.filter((s) => showStageHistory ? s.status === "completed" || s.status === "paused" || s.status === "cancelled" : s.status === "pending" || s.status === "active").length === 0 ? <EmptyHint next="新建阶段并配置方向、篇数、时间段和完成条件">{showStageHistory ? "暂无历史阶段" : "暂无当前阶段"}</EmptyHint> : <div className="plan-stage-list">{stages.filter((s) => showStageHistory ? s.status === "completed" || s.status === "paused" || s.status === "cancelled" : s.status === "pending" || s.status === "active").map((s) => <div className="plan-stage-card" key={s.id}><div className="panel-head"><div><b>{s.name}</b><div className="tiny">{s.start} — {s.end}</div></div><span className="tag gray">{stageLabels[s.status]}</span></div><p>{s.direction || "未填写方向"}</p><div className="plan-stage-metrics"><div><b>{s.count}</b><small>计划篇数</small></div><div><b>{s.confirmed}</b><small>已确认发布</small></div><div><b>{Math.min(100, Math.round(s.confirmed / Math.max(1, s.count) * 100))}%</b><small>发布目标进度</small></div></div><div className="tiny">完成条件：{s.rule}</div><div className="plan-form-actions"><button className="btn sm" onClick={() => setEditingStage({ ...s })}>编辑</button><button className="btn sm danger" onClick={() => void deleteStage(s)}>删除</button></div></div>)}</div>}
      {stageForm && <div className="plan-stage-editor"><div className="panel-head"><b>新建阶段</b><button className="btn sm" onClick={() => setStageForm(false)}>取消</button></div><div className="grid two"><div><label className="form-label">阶段名称</label><input className="field" value={stageName} onChange={(e) => setStageName(e.target.value)}/><label className="form-label" style={{ marginTop: 10 }}>选题方向</label><textarea className="field" rows={3} value={stageDirection} onChange={(e) => setStageDirection(e.target.value)}/></div><div><label className="form-label">目标篇数</label><input className="field" type="number" min={1} value={stageCount} onChange={(e) => setStageCount(Math.max(1, Number(e.target.value) || 1))}/><div className="grid two"><div><label className="form-label">开始日期</label><input className="field" type="date" value={stageStart} onChange={(e) => setStageStart(e.target.value)}/></div><div><label className="form-label">结束日期</label><input className="field" type="date" value={stageEnd} onChange={(e) => setStageEnd(e.target.value)}/></div></div><label className="form-label" style={{ marginTop: 10 }}>完成条件</label><input className="field" value={stageRule} onChange={(e) => setStageRule(e.target.value)}/></div></div><div className="plan-form-actions"><button className="btn primary" onClick={createStage}>保存阶段草稿</button></div></div>}
      {editingStage && <div className="plan-stage-editor"><div className="panel-head"><b>编辑阶段 · {editingStage.name}</b><button className="btn sm" onClick={() => setEditingStage(null)}>取消</button></div><label className="form-label">阶段名称</label><input className="field" value={editingStage.name} onChange={(e) => setEditingStage({ ...editingStage, name: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>选题方向</label><textarea className="field" rows={3} value={editingStage.direction} onChange={(e) => setEditingStage({ ...editingStage, direction: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>目标篇数</label><input className="field" type="number" min={1} value={editingStage.count} onChange={(e) => setEditingStage({ ...editingStage, count: Math.max(1, Number(e.target.value) || 1) })}/><div className="grid two"><div><label className="form-label">开始日期</label><input className="field" type="date" value={editingStage.start} onChange={(e) => setEditingStage({ ...editingStage, start: e.target.value })}/></div><div><label className="form-label">结束日期</label><input className="field" type="date" value={editingStage.end} onChange={(e) => setEditingStage({ ...editingStage, end: e.target.value })}/></div></div><label className="form-label" style={{ marginTop: 10 }}>完成条件</label><input className="field" value={editingStage.rule} onChange={(e) => setEditingStage({ ...editingStage, rule: e.target.value })}/><div className="plan-form-actions"><button className="btn primary" onClick={saveStage}>保存修改</button></div></div>}
    </div>}
  </div>;
}
