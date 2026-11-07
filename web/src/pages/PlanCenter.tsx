import { useEffect, useState } from "react";
import { useApp } from "../AppContext";
import { navigate } from "../nav";
import { api, BusinessDiagnosisReport, Goal, MasterPlan, MasterPlanGenerationMetadata, MasterPlanVersion, PlanResponse, PlanSignatureHistory, SpiralAnalysis, SubPlanStageRecord } from "../api";
import { ALL_PLATFORMS, Platform, platformMeta } from "../platforms";
import EmptyHint from "../components/EmptyHint";
import ProductionLinePanel from "../components/ProductionLinePanel";
import OriginalAssetInventoryPanel from "../components/OriginalAssetInventoryPanel";
import NorthStarResourcePanel from "../components/NorthStarResourcePanel";
import SubgoalManager from "../components/SubgoalManager";
import { businessCalendarDate, formatCalendarDate } from "../calendarDate.mjs";
import StructuredDataView from "../components/StructuredDataView";

type Tab = "master" | "platforms" | "stages" | "goals" | "production-line" | "assets" | "north-star";
type StageStatus = "pending" | "active" | "completed" | "paused" | "cancelled";
type Stage = { id: string; name: string; direction: string; count: number; start: string; end: string; rule: string; status: StageStatus; confirmed: number; progress?: SubPlanStageRecord["progress"]; strategyGoal: string; currentProblem: string; executionStrategy: string; prerequisites: string; inputInfo: string; expectedResult: string; completionCriteria: string; effectEvaluation: string; entryCondition: string; entryConditionMet: boolean; businessCompletionRequired: boolean; businessCompletionStatus: string; effectRequired: boolean; effectStatus: string; dependencies: string[]; latestOutcome?: SubPlanStageRecord["latest_outcome"] };
type Child = { id: string; platform: Platform; name: string; direction: string; count: number; status: "draft" | "review" | "effective" | "stale"; signature: string; versionId: number; versionNo: number; masterPlanVersionId: number; masterPlanVersionNo: number; versionHistory: Array<{ id:number; version_no:number; status:string; master_plan_version_id:number; snapshot:Record<string,unknown> }>; stageRecords: SubPlanStageRecord[] };
const stageLabels: Record<StageStatus, string> = { pending: "待执行", active: "执行中", completed: "已完成", paused: "已暂停", cancelled: "已取消" };
const childLabels = { draft: "草稿", review: "待审核", effective: "已生效", stale: "历史版本 · 需重建" };
const today = () => formatCalendarDate(businessCalendarDate());
const dateAfterDays = (days: number) => {
  const date = businessCalendarDate();
  date.setDate(date.getDate() + days);
  return formatCalendarDate(date);
};
function snapshot(item: PlanSignatureHistory): MasterPlan | null {
  try { return (typeof item.plan_snapshot === "string" ? JSON.parse(item.plan_snapshot) : item.plan_snapshot) as MasterPlan; }
  catch { return null; }
}
function parseGenerationMetadata(item: MasterPlanVersion | undefined): MasterPlanGenerationMetadata | null {
  const note = item?.change_note ?? "";
  const prefix = "AI 母计划草稿：";
  if (!note.startsWith(prefix)) return null;
  try { return JSON.parse(note.slice(prefix.length)) as MasterPlanGenerationMetadata; }
  catch { return null; }
}
function parseMasterPlan(value: string | MasterPlan | undefined): MasterPlan | null {
  if (!value) return null;
  try { return (typeof value === "string" ? JSON.parse(value) : value) as MasterPlan; }
  catch { return null; }
}

export default function PlanCenter() {
  const { showToast } = useApp();
  const [tab, setTab] = useState<Tab>("master");
  const [data, setData] = useState<PlanResponse | null>(null);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [review, setReview] = useState<SpiralAnalysis | null>(null);
  const [adoption, setAdoption] = useState<{ adopted_dimensions: string[]; drafts: Array<{ sub_plan_id: string; platform: string; draft_version: { version_no: number } }>; skipped?: Array<{ sub_plan_id: string; platform: string; status: string; reason: string; detail: string }>; message: string; rebaseStatus?: "awaiting_master_review" | "drafts_created" | "partial" | "failed"; targetMasterPlanVersionId?: number; expectedDraftCount?: number } | null>(null);
  const [pendingAdoptionDimensions, setPendingAdoptionDimensions] = useState<string[]>([]);
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
  const [masterNorthStarVersionId, setMasterNorthStarVersionId] = useState<number | null>(null);
  const [masterNorthStarVersionNo, setMasterNorthStarVersionNo] = useState<number | null>(null);
  const [masterResourceBaseline, setMasterResourceBaseline] = useState<{ captured_at: string; captured_by: string; snapshot: Record<string, unknown> } | null>(null);
  const [masterDraftVersionId, setMasterDraftVersionId] = useState<number | null>(null);
  const [diagnosis, setDiagnosis] = useState<BusinessDiagnosisReport | null>(null);
  const [aiGeneration, setAiGeneration] = useState<MasterPlanGenerationMetadata | null>(null);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [aiGenerationError, setAiGenerationError] = useState("");
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
  const [stageCount, setStageCount] = useState(1);
  const [stageStart, setStageStart] = useState(today());
  const [stageEnd, setStageEnd] = useState(dateAfterDays(30));
  const [stageRule, setStageRule] = useState("达到计划篇数，且所有内容完成审核");
  const [stageStrategyGoal, setStageStrategyGoal] = useState("");
  const [stageCurrentProblem, setStageCurrentProblem] = useState("");
  const [stageExecutionStrategy, setStageExecutionStrategy] = useState("");
  const [stagePrerequisites, setStagePrerequisites] = useState("");
  const [stageInputInfo, setStageInputInfo] = useState("");
  const [stageExpectedResult, setStageExpectedResult] = useState("");
  const [stageEffectEvaluation, setStageEffectEvaluation] = useState("");
  const [stageEntryCondition, setStageEntryCondition] = useState("");
  const [stageEntryConditionMet, setStageEntryConditionMet] = useState(true);
  const [stageBusinessRequired, setStageBusinessRequired] = useState(false);
  const [stageEffectRequired, setStageEffectRequired] = useState(false);
  const [stageDependencies, setStageDependencies] = useState<string[]>([]);
  const [editingStage, setEditingStage] = useState<Stage | null>(null);
  const [conditionReview, setConditionReview] = useState<{
    stageId: number; type: "business" | "effect";
    decision: "passed" | "failed" | "needs_review"; reason: string; evidence: string;
  } | null>(null);
  const [openingRound, setOpeningRound] = useState(false);
  const [pendingRoundLaunch, setPendingRoundLaunch] = useState<{
    roundId: number; roundNumber: number; topicCount: number; platform: Platform;
  } | null>(null);

  const load = async (preferredChildId?: string) => {
    setLoading(true);
    try {
      const [planResult, goalResult, versionResult, subplanResult, diagnosisResult] = await Promise.all([
        api.plans(), api.goals(), api.planVersions(), api.subplans({ page: 1, pageSize: 100 }),
        api.businessDiagnosis(),
      ]);
      const effectiveMaster = versionResult.items.find((item) => item.status === "effective");
      const pendingMaster = versionResult.items.find((item) => item.status === "draft");
      const pendingPlan = parseMasterPlan(pendingMaster?.plan_snapshot);
      const adoptionPrefix = "复盘建议采纳母计划草稿：";
      const adoptionMaster = [pendingMaster, effectiveMaster].find((item) => item?.change_note?.startsWith(adoptionPrefix));
      const adoptionDimensions = adoptionMaster?.change_note?.startsWith(adoptionPrefix)
        ? adoptionMaster.change_note.slice(adoptionPrefix.length).split("、").map((item) => item.trim()).filter(Boolean)
        : [];
      if (adoptionDimensions.length) setPendingAdoptionDimensions(adoptionDimensions);
      setMasterDraftVersionId(pendingMaster?.id ?? null);
      setMasterNorthStarVersionId(effectiveMaster?.north_star_version_id ?? null);
      if (effectiveMaster?.north_star_version_id) {
        try {
          const boundNorthStar = await api.northStarVersion(effectiveMaster.north_star_version_id);
          setMasterNorthStarVersionNo(boundNorthStar.version_no);
        } catch { setMasterNorthStarVersionNo(null); }
      } else {
        setMasterNorthStarVersionNo(null);
      }
      if (effectiveMaster?.id) {
        try { setMasterResourceBaseline(await api.masterPlanResourceBaseline(effectiveMaster.id)); }
        catch { setMasterResourceBaseline(null); }
      } else {
        setMasterResourceBaseline(null);
      }
      setDiagnosis(diagnosisResult.report);
      setAiGeneration(parseGenerationMetadata(pendingMaster) ?? null);
      setData(planResult); setGoals(goalResult);
      setDraft({ ...structuredClone(pendingPlan ?? planResult.plan), content_format: "image_text" });
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
        const parentVersionId = Number(current?.master_plan_version_id ?? identity.master_plan_version_id ?? 0);
        const parentMaster = versionResult.items.find((item) => Number(item.id) === parentVersionId);
        const parentIsCurrent = Boolean(effectiveMaster && parentVersionId === Number(effectiveMaster.id) && parentMaster?.status === "effective");
        const status = !parentIsCurrent
          ? "stale"
          : identity.status === "active" && current?.status === "effective"
            ? "effective" : current?.status === "draft" ? "draft" : "review";
        const versionHistory = detail.versions.map((item) => ({
          id: Number(item.id), version_no: Number(item.version_no), status: String(item.status),
          master_plan_version_id: Number(item.master_plan_version_id ?? 0),
          snapshot: (item.snapshot ?? {}) as Record<string, unknown>,
        }));
        const parentVersionNo = Number(parentMaster?.version_no ?? 0);
        return {
          id: identity.id, platform: identity.platform,
          name: String(snapshot.title ?? identity.title),
          direction: String(snapshot.direction ?? ""),
          count: stageRecords.reduce((sum, stage) => sum + Number(stage.target_count || 0), 0),
          status, signature: planResult.signature,
          versionId: Number(current?.id ?? identity.current_version_id ?? 0),
          versionNo: Number(current?.version_no ?? identity.current_version_no ?? 1),
          masterPlanVersionId: parentVersionId,
          masterPlanVersionNo: parentVersionNo,
          versionHistory,
          stageRecords,
        };
      });
      setChildren(mapped);
      setSelectedChildId((current) => mapped.some((item) => item.id === current && item.status !== "stale")
        ? current : (mapped.find((item) => item.status !== "stale") ?? mapped[0])?.id ?? "");
      const pausedForMaster = effectiveMaster?.source === "review_adoption"
        ? subplanResult.items.filter((item) => item.status === "paused" && Number(item.paused_by_master_version_id) === Number(effectiveMaster.id))
        : [];
      if (pausedForMaster.length > 0 && effectiveMaster) {
        const rebaseDrafts = mapped.filter((item) => item.masterPlanVersionId === Number(effectiveMaster.id) && item.status === "draft");
        const rebaseStatus = rebaseDrafts.length === pausedForMaster.length
          ? "drafts_created" : rebaseDrafts.length > 0 ? "partial" : "failed";
        setAdoption({
          adopted_dimensions: adoptionDimensions,
          drafts: rebaseDrafts.map((item) => ({ sub_plan_id: item.id, platform: item.platform, draft_version: { version_no: item.versionNo } })),
          message: rebaseStatus === "drafts_created"
            ? `母计划已生效；已创建 ${rebaseDrafts.length} 个子计划 rebase 草稿，等待逐项审核。`
            : rebaseStatus === "partial"
              ? `部分成功：已创建 ${rebaseDrafts.length} / ${pausedForMaster.length} 个子计划草稿。剩余平台请在平台子计划页基于当前母计划重建。`
              : "母计划已生效，但子计划 rebase 尚未完成，可重试。",
          rebaseStatus,
          targetMasterPlanVersionId: Number(effectiveMaster.id),
          expectedDraftCount: pausedForMaster.length,
        });
      }
      const preferred = preferredChildId ?? selectedChildId;
      const selectedId = mapped.some((v) => v.id === preferred && v.status !== "stale")
        ? preferred : (mapped.find((v) => v.status !== "stale") ?? mapped[0])?.id ?? "";
      setSelectedChildId(selectedId);
      const selected = mapped.find((item) => item.id === selectedId);
      setStages((selected?.stageRecords ?? []).map((stage) => ({
        id: String(stage.id), name: stage.title, direction: stage.direction ?? "",
        count: Number(stage.target_count), start: stage.start_date ?? "", end: stage.end_date ?? "",
        rule: stage.completion_condition ?? "",
        status: stage.status === "in_progress" ? "active" : stage.status,
        confirmed: Number(stage.confirmed_published_count ?? 0),
        progress: stage.progress,
        strategyGoal: stage.strategy_goal ?? "", currentProblem: stage.current_problem ?? "",
        executionStrategy: stage.execution_strategy ?? "", prerequisites: stage.prerequisites ?? "",
        inputInfo: stage.input_info ?? "", expectedResult: stage.expected_result ?? "",
        completionCriteria: stage.completion_criteria ?? stage.completion_condition ?? "",
        effectEvaluation: stage.effect_evaluation ?? "", entryCondition: stage.entry_condition ?? "",
        entryConditionMet: Number(stage.entry_condition_met ?? 1) !== 0, dependencies: stage.dependencies ?? [],
        businessCompletionRequired: Boolean(stage.business_completion_required),
        businessCompletionStatus: String(stage.business_completion_status ?? "not_required"),
        effectRequired: Boolean(stage.effect_required),
        effectStatus: String(stage.effect_status ?? "not_required"),
        latestOutcome: stage.latest_outcome,
      })));
      try { setReview(await api.gaps()); } catch { setReview(null); }
      setDirty(false); setSaved(false); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "计划数据加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);

  const changeDraft = (value: MasterPlan) => { setDraft(value); setDirty(true); setSaved(false); };
  // Do not mutate the editor or saved-draft pointer until the server confirms persistence.
  const generateAIMasterPlan = async () => {
    if (dirty && !window.confirm("当前母计划有未保存修改。继续生成会在成功后切换到新的 AI 草稿，是否继续？")) return;
    setAiGenerating(true);
    setAiGenerationError("");
    try {
      const result = await api.generateAIMasterPlanDraft();
      const generatedPlan = parseMasterPlan(result.draft.plan_snapshot);
      if (!generatedPlan) throw new Error("服务端返回的母计划草稿无法解析，未覆盖当前编辑内容");
      setDraft({ ...generatedPlan, content_format: "image_text" });
      setMasterDraftVersionId(result.draft.id);
      setAiGeneration(result.generation);
      setDirty(false);
      setSaved(true);
      showToast(`AI 已生成母计划草稿 v${result.draft.version_no}，尚未生效`, "success");
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setAiGenerationError(message);
      showToast("AI 母计划生成失败：" + message, "error");
    } finally {
      setAiGenerating(false);
    }
  };
  const saveDraft = async () => {
    if (!draft) return;
    try {
      const result = await api.createPlanDraft(draft);
      setMasterDraftVersionId(result.draft.id);
      setSaved(true); setDirty(false);
      showToast(`母计划草稿 v${result.draft.version_no} 已写入服务器，尚未生效`, "success");
    } catch (e) { showToast("保存母计划草稿失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const inspectRebaseDrafts = async (targetMasterPlanVersionId: number) => {
    const listed = await api.subplans();
    const details = await Promise.all(listed.items.map((item) => api.subplan(item.id)));
    const drafts = details.flatMap((detail) => detail.versions
      .filter((version) => Number(version.master_plan_version_id) === targetMasterPlanVersionId && version.status === "draft")
      .map((version) => ({
        sub_plan_id: detail.subplan.id,
        platform: detail.subplan.platform,
        draft_version: { version_no: version.version_no },
      })));
    const expected = details.filter((detail) =>
      detail.subplan.status === "paused"
      && Number(detail.subplan.paused_by_master_version_id) === targetMasterPlanVersionId
    ).length;
    return { drafts, expected };
  };
  const submitMasterReview = async () => {
    if (!masterDraftVersionId) { showToast("请先保存一个母计划草稿版本", "error"); return; }
    if (!window.confirm("确认审核通过当前母计划草稿？这会切换生效母计划，并暂停基于旧版本的子计划。")) return;
    try {
      const reviewedVersionId = masterDraftVersionId;
      const result = await api.reviewMasterPlan(reviewedVersionId, "approve");
      const generated = result.subplan_generation;
      if (generated?.status === "rebase_required") {
        const pendingRebase = await inspectRebaseDrafts(reviewedVersionId);
        if (pendingRebase.expected === 0) {
          setAdoption({
            adopted_dimensions: pendingAdoptionDimensions, drafts: [],
            message: "母计划已生效；当前没有需要 rebase 的旧子计划。",
            rebaseStatus: "drafts_created", targetMasterPlanVersionId: reviewedVersionId,
            expectedDraftCount: 0,
          });
          showToast("母计划已生效；没有旧子计划需要 rebase", "success");
        } else try {
          const rebased = await api.adoptGaps(pendingAdoptionDimensions, reviewedVersionId);
          const expectedCount = rebased.expected_draft_count ?? pendingRebase.expected;
          const partial = rebased.status === "partial" || rebased.drafts.length < expectedCount;
          setAdoption({
            ...rebased, rebaseStatus: partial ? "partial" : "drafts_created",
            targetMasterPlanVersionId: reviewedVersionId, expectedDraftCount: expectedCount,
            message: partial
              ? `部分成功：已创建 ${rebased.drafts.length} / ${expectedCount} 个子计划草稿。剩余平台请在平台子计划页基于当前母计划重建。`
              : rebased.message,
          });
          showToast(partial ? "母计划已生效，子计划 rebase 部分成功" : `母计划已生效，已将 ${rebased.drafts.length} 个旧子计划 rebase 为新草稿`, partial ? "error" : "success");
        } catch (rebaseError) {
          const errorMessage = rebaseError instanceof Error ? rebaseError.message : String(rebaseError);
          let observed: Awaited<ReturnType<typeof inspectRebaseDrafts>> | null = null;
          try { observed = await inspectRebaseDrafts(reviewedVersionId); } catch { observed = null; }
          if (observed && observed.drafts.length > 0) {
            const partial = observed.drafts.length < observed.expected;
            setAdoption({
              adopted_dimensions: pendingAdoptionDimensions,
              drafts: observed.drafts,
              message: partial
                ? `部分成功：已创建 ${observed.drafts.length} / ${observed.expected} 个子计划草稿。剩余平台请在平台子计划页基于当前母计划重建；不要重复提交已成功的平台。`
                : `所有 ${observed.drafts.length} 个子计划草稿均已落库；接口响应异常：${errorMessage}`,
              rebaseStatus: partial ? "partial" : "drafts_created",
              targetMasterPlanVersionId: reviewedVersionId,
              expectedDraftCount: observed.expected,
            });
            showToast(partial ? "母计划已生效，子计划 rebase 部分成功；已保留成功项明细" : "母计划已生效，子计划草稿已全部落库", partial ? "error" : "success");
          } else {
            setAdoption((current) => ({
              adopted_dimensions: pendingAdoptionDimensions,
              drafts: current?.drafts ?? [],
              message: errorMessage,
              rebaseStatus: "failed",
              targetMasterPlanVersionId: reviewedVersionId,
            }));
            showToast("母计划已生效，但旧子计划 rebase 未完成，可重试：" + errorMessage, "error");
          }
        }
      } else if (generated?.status === "drafts_created") {
        showToast(`母计划已生效，AI 自动生成 ${generated.items.length} 个平台子计划草稿，等待审核`, "success");
      } else if (generated?.status === "failed") {
        showToast(generated.reason === "master_strategy_snapshot_missing"
          ? "母计划已生效，但缺少绑定的北极星/资源基线历史快照，无法自动拆解；请重新建立并审核完整版本的母计划。"
          : "母计划已生效，但自动拆解失败；可在平台子计划页手动重试 AI 拆解", "error");
      } else {
        showToast("母计划已审核生效", "success");
      }
      await load();
    } catch (e) { showToast("母计划审核失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const rejectMasterDraft = async () => {
    if (!masterDraftVersionId) return;
    const reason = window.prompt("请输入退回原因，便于后续修改：");
    if (!reason?.trim()) return;
    try {
      await api.reviewMasterPlan(masterDraftVersionId, "reject", reason.trim());
      showToast("母计划草稿已退回，当前生效计划保持不变", "success");
      await load();
    } catch (e) {
      showToast("退回母计划草稿失败：" + (e instanceof Error ? e.message : String(e)), "error");
    }
  };
  const deleteMasterPlan = async () => {
    if (!masterVersionId) return;
    const confirmed = window.confirm(
      "确认删除当前生效母计划？系统会保留历史生产记录，并自动恢复上一份曾经生效且未删除的母计划。只有因本次切换暂停的子计划会恢复；人工暂停、已完成和已取消的子计划不会恢复。没有可恢复版本时，删除会被拒绝。"
    );
    if (!confirmed) return;
    try {
      const result = await api.deleteMasterPlan(masterVersionId);
      setMasterDraftVersionId(null);
      showToast(`当前母计划已软删除，已恢复母计划 v${result.restored_version.version_no}`, "success");
      await load();
    } catch (e) {
      showToast("删除母计划失败：" + (e instanceof Error ? e.message : String(e)), "error");
    }
  };
  const resetDraft = () => { if (data) { setDraft({ ...structuredClone(data.plan), content_format: "image_text" }); setDirty(false); setSaved(false); } };
  const adoptGap = async (dimension: string) => {
    try {
      const result = await api.adoptMasterPlan([dimension]);
      setPendingAdoptionDimensions(result.adopted_dimensions);
      setMasterDraftVersionId(result.draft.id);
      setAdoption({
        adopted_dimensions: result.adopted_dimensions, drafts: [], message: result.message,
        rebaseStatus: "awaiting_master_review", targetMasterPlanVersionId: result.draft.id,
      });
      showToast("已创建复盘采纳母计划草稿；审核生效后再将旧子计划 rebase", "success");
      await load();
    } catch (e) { showToast("采纳失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const retryAdoptionRebase = async () => {
    if (!masterVersionId || !pendingAdoptionDimensions.length) return;
    const pendingRebase = await inspectRebaseDrafts(masterVersionId);
    if (pendingRebase.expected === 0) {
      setAdoption((current) => current ? {
        ...current, drafts: [], rebaseStatus: "drafts_created", expectedDraftCount: 0,
        message: "当前没有需要 rebase 的旧子计划。",
      } : current);
      showToast("当前没有需要 rebase 的旧子计划", "success");
      return;
    }
    try {
      const result = await api.adoptGaps(pendingAdoptionDimensions, masterVersionId);
      const expectedCount = result.expected_draft_count ?? pendingRebase.expected;
      const partial = result.status === "partial" || result.drafts.length < expectedCount;
      setAdoption({
        ...result, rebaseStatus: partial ? "partial" : "drafts_created",
        targetMasterPlanVersionId: masterVersionId, expectedDraftCount: expectedCount,
        message: partial
          ? `部分成功：已创建 ${result.drafts.length} / ${expectedCount} 个子计划草稿。未完成的平台请在平台子计划页基于当前母计划重建。`
          : result.message,
      });
      showToast(partial ? "子计划 rebase 部分成功" : `已生成 ${result.drafts.length} 个子计划 rebase 草稿，需逐项审核`, partial ? "error" : "success");
      await load();
    } catch (e) {
      const errorMessage = e instanceof Error ? e.message : String(e);
      let observed: Awaited<ReturnType<typeof inspectRebaseDrafts>> | null = null;
      try { observed = await inspectRebaseDrafts(masterVersionId); } catch { observed = null; }
      if (observed && observed.drafts.length > 0) {
        const partial = observed.drafts.length < observed.expected;
        setAdoption((current) => current ? {
          ...current, drafts: observed!.drafts,
          rebaseStatus: partial ? "partial" : "drafts_created",
          expectedDraftCount: observed!.expected,
          message: partial
            ? `部分成功：已创建 ${observed!.drafts.length} / ${observed!.expected} 个子计划草稿。未完成的平台请在平台子计划页基于当前母计划重建。`
            : `所有 ${observed!.drafts.length} 个子计划草稿均已落库；接口响应异常：${errorMessage}`,
        } : current);
        showToast(partial ? "子计划 rebase 部分成功" : "子计划草稿已全部落库", partial ? "error" : "success");
      } else {
        setAdoption((current) => current ? { ...current, rebaseStatus: "failed", message: errorMessage } : current);
        showToast("子计划 rebase 失败：" + errorMessage, "error");
      }
    }
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
    if (!editingChild || editingChild.status === "stale") return;
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
          strategy_goal: stage.strategy_goal, current_problem: stage.current_problem,
          execution_strategy: stage.execution_strategy, prerequisites: stage.prerequisites,
          input_info: stage.input_info, expected_result: stage.expected_result,
          completion_criteria: stage.completion_criteria, effect_evaluation: stage.effect_evaluation,
          entry_condition: stage.entry_condition, entry_condition_met: stage.entry_condition_met,
          dependencies: stage.dependencies ?? [],
        })),
        change_note: "计划中心编辑子计划",
      });
      setEditingChild(null); showToast("已创建新的子计划草稿版本，需重新审核", "success");
      await load();
    } catch (e) { showToast("保存失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const reviewChild = async (item: Child) => {
    if (item.status === "effective" || item.status === "stale") return;
    if (!window.confirm("确认审核通过该子计划版本？通过后该平台子计划将获得生产授权。")) return;
    try {
      await api.reviewSubplan(item.id, item.versionId, "approve");
      setSelectedChildId(item.id);
      setTab("production-line");
      try {
        await api.aiDraftProductionLine(item.id);
        showToast("子计划已生效，AI 已自动生成生产线草稿；请检查并确认后才能开轮", "success");
      } catch (lineError) {
        showToast("子计划已生效，但 AI 生产线生成失败；可在生产线管理中重试或手工配置：" +
          (lineError instanceof Error ? lineError.message : String(lineError)), "error");
      }
      await load(item.id);
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
          strategy_goal: stageStrategyGoal, current_problem: stageCurrentProblem,
          execution_strategy: stageExecutionStrategy, prerequisites: stagePrerequisites,
          input_info: stageInputInfo, expected_result: stageExpectedResult,
          completion_criteria: stageRule, effect_evaluation: stageEffectEvaluation,
          entry_condition: stageEntryCondition, entry_condition_met: stageEntryConditionMet,
          business_completion_required: stageBusinessRequired, effect_required: stageEffectRequired,
          dependencies: stageDependencies,
        },
      });
      setStageForm(false); setStageName(""); setStageDirection("");
      setStageStrategyGoal(""); setStageCurrentProblem(""); setStageExecutionStrategy("");
      setStagePrerequisites(""); setStageInputInfo(""); setStageExpectedResult("");
      setStageEffectEvaluation(""); setStageEntryCondition(""); setStageEntryConditionMet(true);
      setStageBusinessRequired(false); setStageEffectRequired(false); setStageDependencies([]);
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
          strategy_goal: editingStage.strategyGoal, current_problem: editingStage.currentProblem,
          execution_strategy: editingStage.executionStrategy, prerequisites: editingStage.prerequisites,
          input_info: editingStage.inputInfo, expected_result: editingStage.expectedResult,
          completion_criteria: editingStage.completionCriteria, effect_evaluation: editingStage.effectEvaluation,
          entry_condition: editingStage.entryCondition, entry_condition_met: editingStage.entryConditionMet,
          business_completion_required: editingStage.businessCompletionRequired,
          effect_required: editingStage.effectRequired,
          dependencies: editingStage.dependencies,
        },
      });
      setEditingStage(null); showToast("阶段修改已保存为新草稿版本，需重新审核", "success");
      await load();
    } catch (e) { showToast("保存阶段失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const openStageForm = () => {
    const selected = children.find((item) => item.id === selectedChildId);
    const configuredTarget = Number(data?.plan.topic_count_per_platform ?? 1);
    const existingTarget = Number(selected?.count ?? 0);
    setStageCount(Math.max(1, existingTarget || configuredTarget || 1));
    setStageStart(today());
    setStageEnd(dateAfterDays(30)); setStageEntryConditionMet(true);
    setStageBusinessRequired(false); setStageEffectRequired(false); setStageDependencies([]);
    setStageForm(true);
  };
  const moveStage = async (stageId: string, delta: number) => {
    const child = children.find((item) => item.id === selectedChildId);
    if (!child) return;
    const ordered = [...child.stageRecords].sort((a, b) => a.stage_order - b.stage_order);
    const index = ordered.findIndex((item) => String(item.id) === stageId), target = index + delta;
    if (index < 0 || target < 0 || target >= ordered.length) return;
    [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
    try {
      await api.reorderSubplanStages(child.id, { based_on_version_id: child.versionId, expected_version_no: child.versionNo, stage_ids: ordered.map((item) => item.id) });
      showToast("策略步骤顺序已保存为新草稿版本，需重新审核", "success"); await load(child.id);
    } catch (e) { showToast("排序失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const recordStageOutcome = async (stage: Stage) => {
    const child = children.find((item) => item.id === selectedChildId);
    if (!child) return;
    const raw = window.prompt("输入阶段实际结果 JSON（例如发布数据、用户反馈、关键指标）");
    if (!raw) return;
    try {
      const result = JSON.parse(raw) as Record<string, unknown>;
      const evaluation = window.prompt("输入效果评价（可留空）") ?? "";
      await api.recordSubplanStageOutcome(child.id, Number(stage.id), { version_id: child.versionId, result, evaluation });
      showToast("阶段实际结果已记录，可供后续步骤引用", "success"); await load(child.id);
    } catch (e) { showToast("结果记录失败：" + (e instanceof Error ? e.message : String(e)), "error"); }
  };
  const openStageRound = async () => {
    const child = children.find((item) => item.id === selectedChildId);
    if (!child || child.status !== "effective") {
      showToast("请先选择已审核生效的平台子计划", "error"); return;
    }
    const stage = stages.find((item) => item.status !== "completed" && item.status !== "cancelled");
    if (!stage || stage.status === "paused") { showToast("当前阶段已暂停或没有待执行阶段", "error"); return; }
    const remaining = stage.count - stage.confirmed;
    if (remaining <= 0) { showToast("当前阶段发布数量已达标，请先完成业务条件或效果审核", "error"); return; }
    if (openingRound) return;
    setOpeningRound(true);
    try {
      const created = await api.createSubplanRound(child.id, {
        topic_count: Math.min(50, remaining),
      });
      setPendingRoundLaunch({
        roundId: created.round_id, roundNumber: created.round_number,
        topicCount: created.topic_count, platform: child.platform,
      });
      const queued = await api.runRound(created.round_id, {
        topic_count: created.topic_count, platforms: [child.platform],
      });
      setPendingRoundLaunch(null);
      showToast(`第 ${created.round_number} 轮已进入生产队列`, "success");
      await load(child.id);
    } catch (e) {
      showToast("生产轮次启动失败：" + (e instanceof Error ? e.message : String(e)) +
        "。若轮次已创建，可使用页面中的“重试入队”操作。", "error");
    } finally {
      setOpeningRound(false);
    }
  };
  const retryPendingRoundLaunch = async () => {
    if (!pendingRoundLaunch || openingRound) return;
    setOpeningRound(true);
    try {
      const queued = await api.runRound(pendingRoundLaunch.roundId, {
        topic_count: pendingRoundLaunch.topicCount, platforms: [pendingRoundLaunch.platform],
      });
      showToast(`第 ${pendingRoundLaunch.roundNumber} 轮已进入生产队列`, "success");
      setPendingRoundLaunch(null);
      await load();
    } catch (e) {
      showToast("重新入队失败：" + (e instanceof Error ? e.message : String(e)), "error");
    } finally {
      setOpeningRound(false);
    }
  };
  const currentLaunchStage = stages.find((item) => item.status !== "completed" && item.status !== "cancelled");
  const canLaunchStageRound = Boolean(
    currentLaunchStage && currentLaunchStage.status !== "paused" &&
    currentLaunchStage.count > currentLaunchStage.confirmed
  );
  const submitStageConditionReview = async () => {
    if (!conditionReview) return;
    const child = children.find((item) => item.id === selectedChildId);
    if (!child) return;
    try {
      const evidence = JSON.parse(conditionReview.evidence || "{}") as Record<string, unknown>;
      if (!conditionReview.reason.trim() && Object.keys(evidence).length === 0) {
        showToast("请填写审核原因或提供至少一项证据", "error"); return;
      }
      await api.reviewSubplanStageCondition(child.id, conditionReview.stageId, {
        condition_type: conditionReview.type, decision: conditionReview.decision,
        reason: conditionReview.reason.trim(), evidence,
      });
      setConditionReview(null);
      showToast("阶段条件审核已记录，完成门状态已更新", "success");
      await load(child.id);
    } catch (e) {
      showToast("阶段条件审核失败：" + (e instanceof Error ? e.message : String(e)), "error");
    }
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
  const diagnosisStrategyCurrent = diagnosis?.strategy_is_current ?? diagnosis?.source_is_current ?? false;

  return <div className="page plan-center-page">
    <div className="page-head"><div><div className="eyebrow">内容生产 · 计划体系</div><h1 className="page-title">计划管理</h1><div className="page-desc">主计划确定目标与策略，平台子计划承接平台差异，阶段负责节奏与完成条件，生产轮次按已生效计划执行。</div></div><div className="head-actions"><button className="btn" onClick={() => void load()} disabled={loading}>↻ 刷新</button><button className="btn" onClick={() => navigate("business-diagnosis")}>AI 经营分析</button><button className="btn primary" disabled={!data} onClick={() => { if (data) { setDraft({ ...structuredClone(data.plan), content_format: "image_text" }); setDirty(true); setSaved(false); setTab("master"); } }}>＋ 新建版本草稿</button></div></div>
    <div className="plan-hierarchy"><div className="plan-hierarchy-node active"><b>主计划</b><small>目标 · 策略 · 版本</small></div><span>→</span><div className={"plan-hierarchy-node " + (tab === "platforms" ? "active" : "")}><b>平台子计划</b><small>平台方向 · 产量 · 授权</small></div><span>→</span><div className={"plan-hierarchy-node " + (tab === "stages" ? "active" : "")}><b>阶段</b><small>时间 · 目标 · 完成条件</small></div><span>→</span><div className={"plan-hierarchy-node " + (tab === "production-line" ? "active" : "")}><b>生产线</b><small>AI 编排 · 编辑 · 确认</small></div><span>→</span><div className="plan-hierarchy-node"><b>生产轮次</b><small>执行 · 内容 · 复盘</small></div></div>
    <div className="tabs page-tabs"><button className={"tab " + (tab === "master" ? "active" : "")} onClick={() => setTab("master")}>主计划与版本</button><button className={"tab " + (tab === "platforms" ? "active" : "")} onClick={() => setTab("platforms")}>平台子计划</button><button className={"tab " + (tab === "stages" ? "active" : "")} onClick={() => setTab("stages")}>阶段管理</button><button className={"tab " + (tab === "goals" ? "active" : "")} onClick={() => setTab("goals")}>子目标管理</button><button className={"tab " + (tab === "production-line" ? "active" : "")} onClick={() => setTab("production-line")}>生产线管理</button><button className={"tab " + (tab === "assets" ? "active" : "")} onClick={() => setTab("assets")}>素材盘点</button><button className={"tab " + (tab === "north-star" ? "active" : "")} onClick={() => setTab("north-star")}>北极星与资源基准</button></div>
    {loading && <div className="panel"><div className="loading"><div className="spinner" />正在读取计划…</div></div>}
    {error && <div className="panel"><div className="note" role="alert" style={{ color: "var(--red)" }}>{error}</div><button className="btn sm" onClick={() => void load()}>重试</button></div>}

    {tab === "assets" && <OriginalAssetInventoryPanel />}
    {tab === "north-star" && <NorthStarResourcePanel onOpenAssets={() => setTab("assets")} />}

    {!loading && !error && tab === "master" && data && draft && <>
      <div className="panel">
        <div className="panel-head">
          <div><div className="panel-title">AI 制定母计划</div><div className="panel-sub">基于已确认诊断、当前账号组北极星和资源基线生成不可直接生效的母计划草稿；生产期内容变化不会单独使战略依据失效。</div></div>
          <span className={"tag " + (diagnosis?.status === "confirmed" && diagnosisStrategyCurrent ? "green" : "orange")}>
            {diagnosis?.status === "confirmed" && diagnosisStrategyCurrent ? "诊断已确认 · 战略依据有效" : diagnosis?.status === "stale" || (diagnosis?.status === "confirmed" && !diagnosisStrategyCurrent) ? "战略依据已变化" : diagnosis ? "诊断待确认" : "尚无诊断报告"}
          </span>
        </div>
        {diagnosis ? <div className="grid two">
          <div className="plan-summary-card"><div className="tiny">当前经营诊断</div><b>报告 v{diagnosis.version_no}</b><div className="tiny">状态：{diagnosis.status === "confirmed" ? "已确认" : diagnosis.status === "stale" ? "已过期" : diagnosis.status === "superseded" ? "已被新版本替代" : "待确认"}</div><div className="tiny">确认时间：{diagnosis.confirmed_at ?? "尚未确认"}</div><div className="tiny">诊断模型：{diagnosis.model_name || "—"}</div></div>
          <div className="plan-summary-card"><div className="tiny">依据完整性</div><b>{diagnosisStrategyCurrent ? "北极星与资源基线版本一致" : "战略依据已变化或缺少版本快照"}</b><div className="tiny">关联资料与素材：{diagnosis.source_snapshot?.source_references?.length ?? 0} 项</div><StructuredDataView data={diagnosis.source_snapshot?.inventory_summary ?? {}} title="依据摘要" />{(diagnosis.source_snapshot?.source_references ?? []).map((source) => <div className="tiny" key={source.source_type + ":" + source.source_id}>{source.source_type} · {source.label}</div>)}</div>
        </div> : <div className="note">尚无经营诊断报告。AI 生成需要先完成并确认经营分析；也可直接编辑下方母计划并保存人工草稿，该路径不依赖 AI 模型，但仍要求账号组北极星已建立并确认。</div>}
        {diagnosis && diagnosis.status !== "confirmed" && <div className="note" style={{ marginTop: 10, color: "var(--orange)" }}>最新诊断报告尚未确认。请返回 AI 经营分析页面完成确认，生成入口会在确认后开放。</div>}
        {diagnosis?.status === "confirmed" && !diagnosisStrategyCurrent && <div className="note" style={{ marginTop: 10, color: "var(--orange)" }}>诊断报告绑定的账号组北极星或资源基线已变化，或报告缺少战略版本快照。请重新分析并确认。</div>}
        <div className="plan-form-actions">
          <button className="btn" onClick={() => navigate("business-diagnosis")}>查看经营分析</button>
          <button className="btn primary" onClick={() => void generateAIMasterPlan()} disabled={aiGenerating || diagnosis?.status !== "confirmed" || !diagnosisStrategyCurrent}>
            {aiGenerating ? "正在生成母计划…" : "AI 生成母计划草稿"}
          </button>
        </div>
        {aiGenerationError && <div className="note" role="alert" style={{ marginTop: 10, color: "var(--red)" }}>生成失败：{aiGenerationError}。当前编辑内容和已生效计划未被覆盖，可修复依据或配置后重试。</div>}
        {aiGeneration && <div className="plan-version-detail" style={{ marginTop: 14 }}>
          <div className="panel-head"><b>本次 AI 生成依据与待确认事项</b><span className="tag gray">仅草稿 · 待人工审核</span></div>
          <div className="tiny">经营诊断 v{aiGeneration.diagnosis_version_no} · 北极星 v{aiGeneration.north_star_version_no} · 模型：{aiGeneration.model} · 生成时间：{aiGeneration.generated_at}</div>
          <div className="tiny">资源基线：{String((aiGeneration.resource_baseline.summary as Record<string, number> | undefined)?.confirmed ?? 0)} 项已确认 / {String((aiGeneration.resource_baseline.summary as Record<string, number> | undefined)?.total ?? 0)} 项</div>
          <div style={{ marginTop: 10 }}><b>制定摘要</b><p>{aiGeneration.summary}</p></div>
          <div className="grid two">
            <div><b>业务资料与素材依据</b><StructuredDataView data={aiGeneration.resource_basis} /></div>
            <div><b>风险提示</b><StructuredDataView data={aiGeneration.risks} /></div>
            <div><b>前提与假设</b><StructuredDataView data={aiGeneration.assumptions} /></div>
            <div><b>待用户确认</b><StructuredDataView data={aiGeneration.pending_confirmations} /></div>
          </div>
        </div>}
      </div>
      <div className="panel">
        <div className="panel-head"><div><div className="panel-title">当前主计划</div><div className="panel-sub">历史版本仅作审计；当前生效母计划由唯一生效版本确定，平台执行参数与战略字段分离保存。</div></div><span className={"tag " + (data.source === "plan_settings" ? "green" : "gray")}>{data.source === "plan_settings" ? "服务器已保存" : "由最近轮次推导"}</span></div>
        <div className="grid two"><div className="plan-summary-card"><div className="tiny">计划更新时间</div><b>{data.updated_at ?? "尚未保存"}</b></div><div className="plan-summary-card"><div className="tiny">目标与范围</div><b>{goals.find((g) => g.id === data.plan.cadence?.goal_id)?.goal_text ?? "尚未关联目标"}</b><div className="tiny">启用平台 {platforms.length} 个 · 每平台默认 {data.plan.topic_count_per_platform} 篇/轮</div></div><div className="plan-summary-card"><div className="tiny">战略版本绑定</div><b>{masterNorthStarVersionId ? `账号组北极星 V${masterNorthStarVersionNo ?? "?"}` : "未归属北极星版本"}</b><div className="tiny">{masterResourceBaseline ? `资源基线已固化 · ${masterResourceBaseline.captured_at}` : "该母计划没有资源基线快照"}</div><div className="tiny">{masterResourceBaseline ? `确认资源 ${String((masterResourceBaseline.snapshot.summary as Record<string, number> | undefined)?.confirmed ?? 0)} / ${String((masterResourceBaseline.snapshot.summary as Record<string, number> | undefined)?.total ?? 0)}` : "旧版本可能尚未建立版本绑定"}</div></div></div>
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="panel-head"><div><div className="panel-title">复盘建议与版本关系</div><div className="panel-sub">仅用户可采纳的覆盖缺口可以写回母计划；采纳后会生成新签名，并标记基于旧版本的活跃轮次。</div></div><button className="btn sm" onClick={() => { window.location.search = "?page=gaps"; }}>查看完整复盘</button></div>
          {adoption && <div className="note" style={{ marginBottom: 10 }}>最近采纳：{adoption.adopted_dimensions.join("、")}。{adoption.rebaseStatus === "awaiting_master_review" ? "母计划草稿待审核；审核生效后才会暂停旧子计划并执行 rebase。" : adoption.rebaseStatus === "drafts_created" ? `已创建 ${adoption.drafts.length} 个子计划 rebase 草稿；需逐项审核后生效。` : adoption.rebaseStatus === "partial" ? adoption.message : adoption.rebaseStatus === "failed" ? `rebase 未完成：${adoption.message}` : adoption.message}{adoption.drafts.length > 0 && <div className="tiny">已落库草稿：{adoption.drafts.map((item) => `${platformMeta(item.platform as Platform).label} V${item.draft_version.version_no}`).join("、")}</div>}{(adoption.skipped?.length ?? 0) > 0 && <div className="tiny">跳过项：{adoption.skipped!.map((item) => `${item.platform}（${item.detail}）`).join("；")}</div>}{adoption.rebaseStatus === "failed" && masterVersionId && <button className="btn sm" style={{ marginLeft: 8 }} onClick={() => void retryAdoptionRebase()}>重试子计划 rebase</button>}</div>}
          {(review?.gaps ?? []).filter((g) => g.audience === "user" && g.gap_type === "coverage_gap").length === 0 ? <EmptyHint next="完成一轮内容生产和数据采集后，复盘系统会给出可采纳建议">当前没有可采纳的覆盖缺口建议</EmptyHint> : <div className="list">{(review?.gaps ?? []).filter((g) => g.audience === "user" && g.gap_type === "coverage_gap").map((g) => <div className="task-row" key={g.dimension}><div className="task-main"><div className="task-title">{g.dimension}</div><div className="task-desc">{g.suggestion_text} · 当前 {g.current_count} 条 · 建议 {g.target_count ?? "—"} 条 · 样本 {g.sample_count}</div></div><button className="btn sm primary" onClick={() => void adoptGap(g.dimension)}>采纳并生成新版本</button></div>)}</div>}
        </div>
        <div className="panel-head" style={{ marginTop: 16 }}><div><div className="panel-title">主计划草稿编辑</div><div className="panel-sub">保存会创建服务端不可变草稿版本；只有审核通过后才切换生效计划。</div></div><span className={"tag " + (dirty ? "orange" : saved ? "green" : "gray")}>{dirty ? "有未保存修改" : saved ? "草稿已暂存" : "与当前版本一致"}</span></div>
        <div className="grid two"><div><label className="form-label">每个平台默认选题数</label><input className="field" type="number" min={1} max={50} value={draft.topic_count_per_platform} onChange={(e) => changeDraft({ ...draft, topic_count_per_platform: Math.max(1, Number(e.target.value) || 1) })}/><label className="form-label" style={{ marginTop: 12 }}>关联目标</label><select className="field" value={draft.cadence?.goal_id ?? ""} onChange={(e) => changeDraft({ ...draft, cadence: { ...draft.cadence, goal_id: e.target.value || undefined } })}><option value="">暂不关联</option>{goals.map((g) => <option key={g.id} value={g.id}>{g.goal_text}</option>)}</select><label className="form-label" style={{ marginTop: 12 }}>内容形式</label><div className="field" style={{ color: "var(--muted)" }}>图文</div></div><div><label className="form-label">启用平台</label><div className="plan-platform-toggles">{ALL_PLATFORMS.map((p) => <label className="plan-platform-toggle" key={p}><input type="checkbox" checked={draft.enabled_platforms.includes(p)} onChange={(e) => changeDraft({ ...draft, enabled_platforms: e.target.checked ? [...draft.enabled_platforms, p] : draft.enabled_platforms.filter((v) => v !== p) })}/><span>{platformMeta(p).label}</span></label>)}</div><label className="form-label" style={{ marginTop: 12 }}>选题方向配额</label>{Object.entries(draft.dimension_allocation).map(([name, count]) => <div className="plan-dimension-row" key={name}><span>{name}</span><input className="field" type="number" min={0} value={count} onChange={(e) => changeDraft({ ...draft, dimension_allocation: { ...draft.dimension_allocation, [name]: Math.max(0, Number(e.target.value) || 0) } })}/><button className="btn sm" onClick={() => { const next = { ...draft.dimension_allocation }; delete next[name]; changeDraft({ ...draft, dimension_allocation: next }); }}>移除</button></div>)}<button className="btn sm" style={{ marginTop: 8 }} onClick={() => { const name = window.prompt("输入选题方向名称"); if (name?.trim()) changeDraft({ ...draft, dimension_allocation: { ...draft.dimension_allocation, [name.trim()]: 1 } }); }}>＋ 添加方向</button></div></div>
        <div className="plan-form-actions"><button className="btn" onClick={resetDraft} disabled={!dirty && !saved}>放弃修改</button><button className="btn primary" onClick={saveDraft} disabled={!dirty}>保存草稿</button><button className="btn" onClick={() => void rejectMasterDraft()} disabled={!masterDraftVersionId}>退回草稿</button><button className="btn" onClick={() => void submitMasterReview()} disabled={!masterDraftVersionId}>审核并生效</button><button className="btn danger" onClick={() => void deleteMasterPlan()} disabled={!masterVersionId || dirty || saved}>删除当前母计划并恢复上一版本</button></div>
        {data.stale_rounds.length > 0 && <div className="note" style={{ marginTop: 12, color: "var(--orange)" }}>{data.stale_rounds.length} 个活跃轮次仍基于旧计划，可到内容生产 → 轮次状态查看。</div>}
      </div>
      <div className="panel"><div className="panel-head"><div><div className="panel-title">版本历史</div><div className="panel-sub">可查看不可变的历史计划快照。</div></div><span className="tag gray">{data.history.length} 条记录</span></div>{data.history.length === 0 ? <EmptyHint next="保存母计划后，系统会记录计划签名">暂无版本历史</EmptyHint> : <div className="table-wrap"><table><thead><tr><th>版本</th><th>关联范围</th><th>来源</th><th>记录时间</th><th>操作</th></tr></thead><tbody>{data.history.map((h, i) => <tr key={h.plan_signature + i}><td>版本记录 {i + 1}</td><td>{h.round_number != null ? "第 " + h.round_number + " 轮" : h.round_id != null ? "历史轮次" : "主计划"}</td><td>{h.author ?? "系统"}</td><td>{h.created_at}</td><td><button className="btn sm" onClick={() => setVersion(h)}>查看快照</button></td></tr>)}</tbody></table></div>}{version && <div className="plan-version-detail"><div className="panel-head"><b>历史计划快照</b><button className="btn sm" onClick={() => setVersion(null)}>关闭</button></div>{snapshot(version) ? <div className="plan-version-grid"><span>默认篇数：{snapshot(version)?.topic_count_per_platform}</span><span>启用平台：{snapshot(version)?.enabled_platforms.map((p) => platformMeta(p as Platform).label).join("、")}</span><span>内容形式：{snapshot(version)?.content_format === "image_text" ? "图文" : snapshot(version)?.content_format ?? "—"}</span><span>方向配额：{Object.entries(snapshot(version)?.dimension_allocation ?? {}).map(([k,v]) => k + " " + v).join("、") || "未设置"}</span></div> : <div className="note">历史快照无法解析。</div>}</div>}</div>
    </>}

    {!loading && !error && tab === "platforms" && data && <>
      <div className="panel"><div className="panel-head"><div><div className="panel-title">平台子计划</div><div className="panel-sub">各平台独立维护方向、目标篇数与执行授权，并引用来源主计划版本。</div></div><div className="head-actions"><button className="btn" onClick={async () => { if (!masterVersionId) { showToast("请先审核生效母计划", "error"); return; } try { const result = await api.aiDraftSubplans(masterVersionId, platforms); showToast(result.already_exists ? "所选平台均已有子计划，无需重复拆解" : `AI 已生成 ${result.items.length} 个子计划草稿，需人工审核`, result.already_exists ? "info" : "success"); await load(); } catch (e) { showToast("AI 拆解失败：" + (e instanceof Error ? e.message : String(e)), "error"); } }}>AI 拆解</button><button className="btn primary" onClick={() => { setChildForm(true); setChildPlatform(platforms[0] ?? "xhs"); setChildCount(data.plan.topic_count_per_platform); }}>＋ 手工创建</button></div></div><div className="note">子计划和版本均持久化在当前账号组数据库；草稿不会自动获得生产授权。</div><div className="grid two" style={{ marginTop: 12 }}>{platforms.map((p) => { const child = children.find((c) => c.platform === p); return <div className="plan-child-card" key={p}><div className="panel-head"><b>{platformMeta(p).label}</b><span className="tag gray">{child ? childLabels[child.status] : "尚未创建子计划"}</span></div>{child ? <><h3>{child.name}</h3><p>{child.direction || "未填写方向"}</p><div className="tiny">目标 {child.count} 篇 · 来源母计划 V{child.masterPlanVersionNo || "—"} · 子计划 V{child.versionNo}</div>{child.status === "stale" && <div className="note" role="status">该子计划绑定的母计划版本已归档，不能继续审核、编辑或生产。请基于当前生效母计划重新创建子计划；历史阶段和生产记录仍保留。</div>}{child.versionHistory.length > 1 && <div className="tiny">最近版本差异：{Object.keys({ ...child.versionHistory[0].snapshot, ...child.versionHistory[1].snapshot }).filter((key) => JSON.stringify(child.versionHistory[0].snapshot[key]) !== JSON.stringify(child.versionHistory[1].snapshot[key])).join("、") || "无字段变化"}（V{child.versionHistory[1].version_no} → V{child.versionHistory[0].version_no}）</div>}<div className="plan-form-actions">{child.status === "stale" ? <button className="btn sm primary" disabled={!masterVersionId} onClick={() => { setChildForm(true); setChildPlatform(child.platform); setChildName(`${child.name}（新母计划）`); setChildDirection(child.direction); setChildCount(Math.max(1, child.count)); }}>基于当前母计划重建</button> : <><button className="btn sm" onClick={() => setEditingChild({ ...child })}>编辑</button><button className="btn sm" onClick={() => reviewChild(child)}>{child.status === "draft" ? "提交审核" : child.status === "review" ? "审核并生效" : "已生效"}</button></>}{child.status === "effective" && <button className="btn sm primary" onClick={() => { setSelectedChildId(child.id); setTab("production-line"); }}>管理生产线 →</button>}</div></> : <><p>继承主计划目标与约束；当前没有独立子计划记录。</p><button className="btn sm" onClick={() => { setChildForm(true); setChildPlatform(p); setChildCount(data.plan.topic_count_per_platform); }}>创建该平台子计划</button></>}</div>; })}</div></div>
      {childForm && <div className="panel"><div className="panel-head"><b>手工创建子计划</b><button className="btn sm" onClick={() => setChildForm(false)}>取消</button></div><div className="grid two"><div><label className="form-label">平台</label><select className="field" value={childPlatform} onChange={(e) => setChildPlatform(e.target.value as Platform)}>{ALL_PLATFORMS.map((p) => <option value={p} key={p}>{platformMeta(p).label}</option>)}</select><label className="form-label" style={{ marginTop: 10 }}>名称</label><input className="field" value={childName} onChange={(e) => setChildName(e.target.value)}/></div><div><label className="form-label">选题方向</label><textarea className="field" rows={3} value={childDirection} onChange={(e) => setChildDirection(e.target.value)}/><label className="form-label" style={{ marginTop: 10 }}>目标篇数</label><input className="field" type="number" min={1} value={childCount} onChange={(e) => setChildCount(Math.max(1, Number(e.target.value) || 1))}/></div></div><div className="plan-form-actions"><button className="btn primary" onClick={createChild}>保存子计划草稿</button></div></div>}
      {editingChild && <div className="panel"><div className="panel-head"><b>编辑子计划 · {platformMeta(editingChild.platform).label}</b><button className="btn sm" onClick={() => setEditingChild(null)}>取消</button></div><label className="form-label">名称</label><input className="field" value={editingChild.name} onChange={(e) => setEditingChild({ ...editingChild, name: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>方向</label><textarea className="field" rows={3} value={editingChild.direction} onChange={(e) => setEditingChild({ ...editingChild, direction: e.target.value })}/><div className="note">子计划总目标篇数由各阶段目标合计；请在「阶段管理」中调整阶段篇数。</div><div className="plan-form-actions"><button className="btn primary" onClick={saveChild}>保存修改</button></div></div>}
    </>}

    {!loading && !error && tab === "production-line" && <>
      <div className="panel">
        <div className="panel-head"><div><div className="panel-title">选择平台子计划</div><div className="panel-sub">每个已审核生效的子计划对应一条独立生产线。</div></div></div>
        <select className="field" value={selectedChildId} onChange={(e) => setSelectedChildId(e.target.value)}>
          <option value="">请选择已生效子计划</option>
          {children.filter((child) => child.status === "effective").map((child) =>
            <option key={child.id} value={child.id}>{platformMeta(child.platform).label} · {child.name}</option>)}
        </select>
        {children.filter((child) => child.status === "effective").length === 0 &&
          <EmptyHint next="先完成母计划和平台子计划审核">当前没有已生效的平台子计划，暂不能建立生产线</EmptyHint>}
      </div>
      {children.find((child) => child.id === selectedChildId && child.status === "effective") && (() => {
        const child = children.find((item) => item.id === selectedChildId)!;
        return <ProductionLinePanel subPlanId={child.id} platform={child.platform} subplanTitle={child.name}/>;
      })()}
    </>}
    {!loading && !error && tab === "goals" && <>{selectedChildId && children.find((item) => item.id === selectedChildId)?.status === "stale" ? <div className="panel"><div className="note">当前子计划属于已归档母计划版本，历史目标仅供查看，不可继续编辑。请到「平台子计划」基于当前母计划重建。</div></div> : selectedChildId ? <SubgoalManager subPlanId={selectedChildId}/> : <div className="panel"><EmptyHint next="先选择一个平台子计划">请先选择平台子计划</EmptyHint></div>}</>}
    {!loading && !error && tab === "stages" && <div className="panel"><div className="panel-head"><div><div className="panel-title">阶段管理</div><div className="panel-sub">按策略步骤管理目标、问题、执行方法、依赖、进入条件、预期结果与实际效果。</div></div><div className="head-actions"><button className="btn" onClick={() => setShowStageHistory((v) => !v)}>{showStageHistory ? "返回当前阶段" : "查看历史阶段"}</button><button className="btn" disabled={openingRound || children.find((item) => item.id === selectedChildId)?.status !== "effective" || showStageHistory || !canLaunchStageRound} onClick={() => void openStageRound()}>{openingRound ? "正在启动…" : "启动当前阶段生产"}</button><button className="btn primary" disabled={!selectedChildId || children.find((item) => item.id === selectedChildId)?.status === "stale"} onClick={openStageForm}>＋ 新建阶段</button></div></div><div className="note">阶段变更会创建不可变子计划草稿版本；需要重新审核后才生效。阶段进度以独立计划指标为准，内容发布数仅作参考。</div>{pendingRoundLaunch && <div className="note" style={{ marginTop: 10 }}>第 {pendingRoundLaunch.roundNumber} 轮已创建，但尚未确认进入队列。<button className="btn sm" style={{ marginLeft: 8 }} disabled={openingRound} onClick={() => void retryPendingRoundLaunch()}>重试入队</button></div>}<div className="form-row"><label className="form-label" htmlFor="stage-subplan-select">所属平台子计划</label><select id="stage-subplan-select" className="field" value={selectedChildId} onChange={(e) => { setSelectedChildId(e.target.value); const child = children.find((item) => item.id === e.target.value); setStages((child?.stageRecords ?? []).map((stage) => ({ id: String(stage.id), name: stage.title, direction: stage.direction ?? "", count: Number(stage.target_count), start: stage.start_date ?? "", end: stage.end_date ?? "", rule: stage.completion_condition ?? "", status: stage.status === "in_progress" ? "active" : stage.status, confirmed: Number(stage.confirmed_published_count ?? 0), progress: stage.progress, strategyGoal: stage.strategy_goal ?? "", currentProblem: stage.current_problem ?? "", executionStrategy: stage.execution_strategy ?? "", prerequisites: stage.prerequisites ?? "", inputInfo: stage.input_info ?? "", expectedResult: stage.expected_result ?? "", completionCriteria: stage.completion_criteria ?? stage.completion_condition ?? "", effectEvaluation: stage.effect_evaluation ?? "", entryCondition: stage.entry_condition ?? "", entryConditionMet: Number(stage.entry_condition_met ?? 1) !== 0, businessCompletionRequired: Boolean(stage.business_completion_required), businessCompletionStatus: String(stage.business_completion_status ?? "not_required"), effectRequired: Boolean(stage.effect_required), effectStatus: String(stage.effect_status ?? "not_required"), dependencies: stage.dependencies ?? [], latestOutcome: stage.latest_outcome }))); setShowStageHistory(false); }}><option value="">请选择子计划</option>{children.filter((child) => child.status !== "stale").map((child) => <option key={child.id} value={child.id}>{platformMeta(child.platform).label} · {child.name}</option>)}</select></div>{stages.filter((s) => showStageHistory ? s.status === "completed" || s.status === "paused" || s.status === "cancelled" : s.status === "pending" || s.status === "active").length === 0 ? <EmptyHint next="新建阶段并配置方向、篇数、时间段和完成条件">{showStageHistory ? "暂无历史阶段" : "暂无当前阶段"}</EmptyHint> : <div className="plan-stage-list">{stages.filter((s) => showStageHistory ? s.status === "completed" || s.status === "paused" || s.status === "cancelled" : s.status === "pending" || s.status === "active").map((s) => <div className="plan-stage-card" key={s.id}><div className="panel-head"><div><b>{s.name}</b><div className="tiny">{s.start} — {s.end}</div></div><span className="tag gray">{stageLabels[s.status]}</span></div><p>{s.direction || "未填写方向"}</p><div className="plan-stage-metrics"><div><b>{s.count}</b><small>计划篇数</small></div><div><b>{s.progress?.basis === "metric" && s.progress.achievement_rate != null ? `${Math.round(s.progress.achievement_rate * 100)}%` : "—"}</b><small>{s.progress?.basis === "metric" ? "计划指标达成率" : "计划指标待配置"}</small></div><div><b>{s.confirmed}</b><small>内容发布数（参考）</small></div></div>{s.progress?.basis === "metric" && s.progress.target_metric && <div className="tiny">指标：{s.progress.target_metric} · 目标 {s.progress.target_value ?? "—"} · 当前 {s.progress.current_value ?? "数据待采集"}</div>}<div className="tiny">完成条件：{s.rule}</div>{s.strategyGoal && <div className="tiny">策略目标：{s.strategyGoal}</div>}{s.executionStrategy && <p>{s.executionStrategy}</p>}{s.dependencies.length > 0 && <div className="tiny">前置步骤：{s.dependencies.join("、")}</div>}{s.latestOutcome && <div className="note">最近实际结果：<StructuredDataView data={s.latestOutcome.result} /> · {s.latestOutcome.evaluation}</div>}
      {(s.businessCompletionRequired || s.effectRequired) && <div className="note">业务完成确认：{s.businessCompletionRequired ? (s.businessCompletionStatus === "passed" ? "已通过" : s.businessCompletionStatus === "failed" ? "未通过" : s.businessCompletionStatus === "needs_review" ? "待复核" : "待确认") : "不要求"}；效果评估：{s.effectRequired ? (s.effectStatus === "passed" ? "已通过" : s.effectStatus === "failed" ? "未通过" : s.effectStatus === "needs_review" ? "待复核" : "待评估") : "不要求"}</div>}
      {(s.businessCompletionRequired || s.effectRequired) && <div className="plan-form-actions">{s.businessCompletionRequired && <button className="btn sm" onClick={() => setConditionReview({ stageId: Number(s.id), type: "business", decision: "passed", reason: "", evidence: "{}" })}>审核业务完成条件</button>}{s.effectRequired && <button className="btn sm" onClick={() => setConditionReview({ stageId: Number(s.id), type: "effect", decision: "passed", reason: "", evidence: "{}" })}>审核效果</button>}</div>}
      {conditionReview?.stageId === Number(s.id) && <div className="plan-stage-editor"><div className="panel-head"><b>{conditionReview.type === "business" ? "业务完成条件审核" : "效果评估审核"}</b><button className="btn sm" onClick={() => setConditionReview(null)}>取消</button></div><label className="form-label">审核结论</label><select className="field" value={conditionReview.decision} onChange={(e) => setConditionReview({ ...conditionReview, decision: e.target.value as "passed" | "failed" | "needs_review" })}><option value="passed">通过</option><option value="failed">未通过</option><option value="needs_review">需要补充材料/复核</option></select><label className="form-label" style={{ marginTop: 8 }}>原因/建议（未通过或待复核必填）</label><textarea className="field" rows={2} value={conditionReview.reason} onChange={(e) => setConditionReview({ ...conditionReview, reason: e.target.value })}/><label className="form-label" style={{ marginTop: 8 }}>证据 JSON（可填写指标、链接或数据摘要）</label><textarea className="field" rows={3} value={conditionReview.evidence} onChange={(e) => setConditionReview({ ...conditionReview, evidence: e.target.value })}/><div className="plan-form-actions"><button className="btn primary" onClick={() => void submitStageConditionReview()}>提交审核</button></div></div>}
      <div className="plan-form-actions"><button className="btn sm" onClick={() => setEditingStage({ ...s })}>编辑策略</button><button className="btn sm" disabled={s.status !== "completed"} onClick={() => void recordStageOutcome(s)}>记录实际结果</button><button className="btn sm" disabled={stages.findIndex((v) => v.id === s.id) === 0} onClick={() => void moveStage(s.id, -1)}>上移</button><button className="btn sm" disabled={stages.findIndex((v) => v.id === s.id) === stages.length - 1} onClick={() => void moveStage(s.id, 1)}>下移</button><button className="btn sm danger" onClick={() => void deleteStage(s)}>删除</button></div></div>)}</div>}
      {stageForm && <div className="plan-stage-editor"><div className="panel-head"><b>新建阶段</b><button className="btn sm" onClick={() => setStageForm(false)}>取消</button></div><div className="grid two"><div><label className="form-label">阶段名称</label><input className="field" value={stageName} onChange={(e) => setStageName(e.target.value)}/><label className="form-label" style={{ marginTop: 10 }}>选题方向</label><textarea className="field" rows={3} value={stageDirection} onChange={(e) => setStageDirection(e.target.value)}/></div><div><label className="form-label">目标篇数</label><input className="field" type="number" min={1} value={stageCount} onChange={(e) => setStageCount(Math.max(1, Number(e.target.value) || 1))}/><div className="grid two"><div><label className="form-label">开始日期</label><input className="field" type="date" value={stageStart} onChange={(e) => setStageStart(e.target.value)}/></div><div><label className="form-label">结束日期</label><input className="field" type="date" value={stageEnd} onChange={(e) => setStageEnd(e.target.value)}/></div></div><label className="form-label" style={{ marginTop: 10 }}>完成条件</label><input className="field" value={stageRule} onChange={(e) => setStageRule(e.target.value)}/></div></div>
      <div className="grid two" style={{ marginTop: 12 }}><div><label className="form-label">策略目标</label><textarea className="field" rows={2} value={stageStrategyGoal} onChange={(e) => setStageStrategyGoal(e.target.value)}/><label className="form-label">当前问题</label><textarea className="field" rows={2} value={stageCurrentProblem} onChange={(e) => setStageCurrentProblem(e.target.value)}/><label className="form-label">执行策略</label><textarea className="field" rows={3} value={stageExecutionStrategy} onChange={(e) => setStageExecutionStrategy(e.target.value)}/><label className="form-label">前置条件</label><textarea className="field" rows={2} value={stagePrerequisites} onChange={(e) => setStagePrerequisites(e.target.value)}/></div><div><label className="form-label">输入信息</label><textarea className="field" rows={2} value={stageInputInfo} onChange={(e) => setStageInputInfo(e.target.value)}/><label className="form-label">预期结果</label><textarea className="field" rows={2} value={stageExpectedResult} onChange={(e) => setStageExpectedResult(e.target.value)}/><label className="form-label">效果评估方法</label><textarea className="field" rows={2} value={stageEffectEvaluation} onChange={(e) => setStageEffectEvaluation(e.target.value)}/><label className="form-label">进入条件</label><textarea className="field" rows={2} value={stageEntryCondition} onChange={(e) => setStageEntryCondition(e.target.value)}/><label className="plan-platform-toggle"><input type="checkbox" checked={stageEntryConditionMet} onChange={(e) => setStageEntryConditionMet(e.target.checked)}/>进入条件已满足</label><label className="plan-platform-toggle"><input type="checkbox" checked={stageBusinessRequired} onChange={(e) => setStageBusinessRequired(e.target.checked)}/>必须人工确认业务完成条件（默认关闭）</label><label className="plan-platform-toggle"><input type="checkbox" checked={stageEffectRequired} onChange={(e) => setStageEffectRequired(e.target.checked)}/>必须通过效果评估</label><label className="form-label">前置步骤</label><select className="field" multiple value={stageDependencies} onChange={(e) => setStageDependencies(Array.from(e.target.selectedOptions, (o) => o.value))}>{(children.find((item) => item.id === selectedChildId)?.stageRecords ?? []).map((v) => <option key={v.stage_key} value={v.stage_key}>{v.title}</option>)}</select></div></div><div className="plan-form-actions"><button className="btn primary" onClick={createStage}>保存阶段草稿</button></div></div>}
      {editingStage && <div className="plan-stage-editor"><div className="panel-head"><b>编辑阶段 · {editingStage.name}</b><button className="btn sm" onClick={() => setEditingStage(null)}>取消</button></div><label className="form-label">阶段名称</label><input className="field" value={editingStage.name} onChange={(e) => setEditingStage({ ...editingStage, name: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>选题方向</label><textarea className="field" rows={3} value={editingStage.direction} onChange={(e) => setEditingStage({ ...editingStage, direction: e.target.value })}/><label className="form-label" style={{ marginTop: 10 }}>目标篇数</label><input className="field" type="number" min={1} value={editingStage.count} onChange={(e) => setEditingStage({ ...editingStage, count: Math.max(1, Number(e.target.value) || 1) })}/><div className="grid two"><div><label className="form-label">开始日期</label><input className="field" type="date" value={editingStage.start} onChange={(e) => setEditingStage({ ...editingStage, start: e.target.value })}/></div><div><label className="form-label">结束日期</label><input className="field" type="date" value={editingStage.end} onChange={(e) => setEditingStage({ ...editingStage, end: e.target.value })}/></div></div><label className="form-label" style={{ marginTop: 10 }}>完成条件</label><input className="field" value={editingStage.rule} onChange={(e) => setEditingStage({ ...editingStage, rule: e.target.value, completionCriteria: e.target.value })}/>
      <div className="grid two" style={{ marginTop: 12 }}><div><label className="form-label">策略目标</label><textarea className="field" rows={2} value={editingStage.strategyGoal} onChange={(e) => setEditingStage({ ...editingStage, strategyGoal: e.target.value })}/><label className="form-label">当前问题</label><textarea className="field" rows={2} value={editingStage.currentProblem} onChange={(e) => setEditingStage({ ...editingStage, currentProblem: e.target.value })}/><label className="form-label">执行策略</label><textarea className="field" rows={3} value={editingStage.executionStrategy} onChange={(e) => setEditingStage({ ...editingStage, executionStrategy: e.target.value })}/><label className="form-label">前置条件</label><textarea className="field" rows={2} value={editingStage.prerequisites} onChange={(e) => setEditingStage({ ...editingStage, prerequisites: e.target.value })}/></div>
      <div><label className="form-label">输入信息</label><textarea className="field" rows={2} value={editingStage.inputInfo} onChange={(e) => setEditingStage({ ...editingStage, inputInfo: e.target.value })}/><label className="form-label">预期结果</label><textarea className="field" rows={2} value={editingStage.expectedResult} onChange={(e) => setEditingStage({ ...editingStage, expectedResult: e.target.value })}/><label className="form-label">效果评估方法</label><textarea className="field" rows={2} value={editingStage.effectEvaluation} onChange={(e) => setEditingStage({ ...editingStage, effectEvaluation: e.target.value })}/><label className="form-label">进入条件</label><textarea className="field" rows={2} value={editingStage.entryCondition} onChange={(e) => setEditingStage({ ...editingStage, entryCondition: e.target.value })}/><label className="plan-platform-toggle"><input type="checkbox" checked={editingStage.businessCompletionRequired} onChange={(e) => setEditingStage({ ...editingStage, businessCompletionRequired: e.target.checked })}/>必须人工确认业务完成条件</label><label className="plan-platform-toggle"><input type="checkbox" checked={editingStage.effectRequired} onChange={(e) => setEditingStage({ ...editingStage, effectRequired: e.target.checked })}/>必须通过效果评估</label><label className="form-label">前置步骤</label><select className="field" multiple value={editingStage.dependencies} onChange={(e) => setEditingStage({ ...editingStage, dependencies: Array.from(e.target.selectedOptions, (o) => o.value) })}>{(children.find((item) => item.id === selectedChildId)?.stageRecords ?? []).filter((v) => String(v.id) !== editingStage.id).map((v) => <option key={v.stage_key} value={v.stage_key}>{v.title}</option>)}</select><label className="plan-platform-toggle"><input type="checkbox" checked={editingStage.entryConditionMet} onChange={(e) => setEditingStage({ ...editingStage, entryConditionMet: e.target.checked })}/>进入条件已满足</label></div></div><div className="plan-form-actions"><button className="btn primary" onClick={saveStage}>保存修改</button></div></div>}
    </div>}
  </div>;
}
