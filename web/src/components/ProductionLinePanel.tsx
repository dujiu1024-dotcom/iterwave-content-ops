import { useCallback, useEffect, useState } from "react";
import { api, ProductionLineDefinition, ProductionLineRecord, ProductionLineVersion } from "../api";
import { Platform, platformMeta } from "../platforms";
import { useApp } from "../AppContext";

interface Props {
  subPlanId: string;
  platform: Platform;
  subplanTitle: string;
}

function defaultDefinition(stages: Array<{ stage_key: string; title: string; target_count: number; execution_strategy?: string; strategy_goal?: string }>): ProductionLineDefinition {
  return {
    schema_version: 1,
    title: "平台内容生产线",
    description: "按已审核的子计划策略步骤组织生产，所有产物进入人工审核，不自动发布。",
    execution_mode: "sequential",
    stages: stages.map((stage, index) => ({
      stage_key: stage.stage_key,
      stage_order: index + 1,
      stage_title: stage.title,
      batch_size: Math.max(1, Math.min(5, Number(stage.target_count) || 1)),
      production_goal: stage.strategy_goal || stage.title,
      instructions: stage.execution_strategy || "",
      cadence: "",
    })),
  };
}

export default function ProductionLinePanel({ subPlanId, platform, subplanTitle }: Props) {
  const { showToast } = useApp();
  const [line, setLine] = useState<ProductionLineRecord | null>(null);
  const [selected, setSelected] = useState<ProductionLineVersion | null>(null);
  const [definition, setDefinition] = useState<ProductionLineDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.productionLine(subPlanId);
      const nextLine = result.production_line;
      setLine(nextLine);
      const versions = nextLine?.versions ?? [];
      const nextVersion = versions.find((v) => v.status === "draft")
        ?? versions.find((v) => v.id === nextLine?.current_version_id)
        ?? versions[0] ?? null;
      setSelected(nextVersion);
      if (nextVersion) {
        setDefinition(structuredClone(nextVersion.definition));
      } else {
        const detail = await api.subplan(subPlanId);
        const current = detail.versions.find((v) => v.id === detail.subplan.current_version_id);
        setDefinition(defaultDefinition((current?.stages ?? []).map((stage) => ({
          stage_key: stage.stage_key, title: stage.title, target_count: stage.target_count,
          execution_strategy: stage.execution_strategy, strategy_goal: stage.strategy_goal,
        }))));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [subPlanId]);

  useEffect(() => { void load(); }, [load]);

  const run = async (action: () => Promise<unknown>, success: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      showToast(success, "success");
      await load();
    } catch (e) {
      showToast("操作失败：" + (e instanceof Error ? e.message : String(e)), "error");
    } finally {
      setBusy(false);
    }
  };

  const generateAI = () => run(async () => {
    const result = await api.aiDraftProductionLine(subPlanId);
    setLine(result.production_line);
    setSelected(result.draft);
    setDefinition(structuredClone(result.draft.definition));
  }, "AI 已生成生产线草稿，等待人工检查和确认");

  const saveDraft = () => {
    if (!definition) return;
    void run(async () => {
      const result = await api.saveProductionLineDraft(subPlanId, definition, selected?.id);
      setSelected(result.draft);
      setDefinition(structuredClone(result.draft.definition));
    }, "生产线修改已保存为新草稿版本");
  };

  const review = (action: "approve" | "reject") => {
    if (!line || !selected) return;
    if (action === "approve" && !window.confirm("确认该生产线版本？确认后才允许为此子计划创建生产轮次。")) return;
    void run(() => api.reviewProductionLine(line.id, selected.id, action,
      action === "reject" ? "用户拒绝该生产线版本" : undefined),
    action === "approve" ? "生产线已确认生效" : "生产线草稿已拒绝");
  };

  const updateStage = (index: number, patch: Partial<ProductionLineDefinition["stages"][number]>) => {
    setDefinition((current) => current ? ({
      ...current,
      stages: current.stages.map((stage, i) => i === index ? { ...stage, ...patch } : stage),
    }) : current);
  };

  if (loading) return <div className="panel"><div className="loading">正在读取生产线…</div></div>;
  return <section className="panel">
    <div className="panel-head">
      <div>
        <div className="panel-title">生产线 · {platformMeta(platform).label}</div>
        <div className="panel-sub">{subplanTitle} · 独立于策略步骤和生产轮次的执行编排</div>
      </div>
      <span className={"tag " + (line?.is_stale ? "orange" : line?.status === "active" ? "green" : line?.status === "paused" ? "orange" : "gray")}>
        {line?.is_stale ? "绑定版本已过期" : line?.status === "active" ? "已确认生效" : line?.status === "paused" ? "已暂停" : line ? line.status : "尚未建立"}
      </span>
    </div>
    {error && <div className="note" role="alert">{error}</div>}
    {!line && <div className="note">当前子计划尚无生产线。可以让 AI 根据已审核策略步骤生成草稿，也可以先建立人工配置。</div>}
    {line?.is_stale && <div className="note" role="alert">子计划已经切换到新版本，当前生产线仍绑定旧版策略。系统已阻止继续开轮；请基于当前子计划版本重新生成或修订生产线并重新确认。</div>}
    {definition && <>
      <label className="form-label">生产线名称</label>
      <input className="field" value={definition.title} onChange={(e) => setDefinition({ ...definition, title: e.target.value })}/>
      <label className="form-label" style={{ marginTop: 10 }}>生产线说明</label>
      <textarea className="field" rows={2} value={definition.description} onChange={(e) => setDefinition({ ...definition, description: e.target.value })}/>
      <div className="panel-head" style={{ marginTop: 14 }}><b>策略步骤与生产配置</b><span className="tiny">{definition.stages.length} 个步骤 · 顺序继承子计划</span></div>
      {definition.stages.map((stage, index) => <div className="plan-stage-card" key={stage.stage_key}>
        <div className="panel-head"><b>{index + 1}. {stage.stage_title}</b><span className="tiny">策略步骤 {stage.stage_key.slice(0, 8)}</span></div>
        <label className="form-label">生产目标</label>
        <textarea className="field" rows={2} value={stage.production_goal} onChange={(e) => updateStage(index, { production_goal: e.target.value })}/>
        <label className="form-label" style={{ marginTop: 8 }}>执行指令</label>
        <textarea className="field" rows={3} value={stage.instructions} onChange={(e) => updateStage(index, { instructions: e.target.value })}/>
        <div className="grid two">
          <div><label className="form-label">每轮生产数量</label><input className="field" type="number" min={1} max={1000} value={stage.batch_size} onChange={(e) => updateStage(index, { batch_size: Math.max(1, Number(e.target.value) || 1) })}/></div>
          <div><label className="form-label">执行节奏</label><input className="field" value={stage.cadence} onChange={(e) => updateStage(index, { cadence: e.target.value })} placeholder="例如：每周 3 轮"/></div>
        </div>
      </div>)}
      <div className="plan-form-actions">
        <button className="btn" onClick={() => void load()} disabled={busy}>刷新</button>
        <button className="btn" onClick={generateAI} disabled={busy}>{busy ? "处理中…" : line ? "AI 重新生成草稿" : "AI 生成生产线"}</button>
        <button className="btn primary" onClick={saveDraft} disabled={busy || !definition.stages.length}>保存为草稿</button>
        {selected?.status === "draft" && <button className="btn primary" onClick={() => review("approve")} disabled={busy}>确认并启用生产线</button>}
        {selected?.status === "draft" && <button className="btn danger" onClick={() => review("reject")} disabled={busy}>拒绝草稿</button>}
        {line?.status === "active" && <button className="btn" onClick={() => void run(() => api.setProductionLineStatus(subPlanId, "paused", "人工暂停"), "生产线已暂停")} disabled={busy}>暂停生产线</button>}
        {line?.status === "paused" && <button className="btn" onClick={() => void run(() => api.setProductionLineStatus(subPlanId, "active"), "生产线已恢复")} disabled={busy}>恢复生产线</button>}
      </div>
      {line?.versions && line.versions.length > 1 && <div className="tiny" style={{ marginTop: 12 }}>历史版本：{line.versions.map((v) => `v${v.version_no} · ${v.status}`).join("；")}</div>}
    </>}
  </section>;
}
