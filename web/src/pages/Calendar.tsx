import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../AppContext";
import EmptyHint from "../components/EmptyHint";
import { api, ContentPlan, Platform, ScheduleEvent, UnscheduledDraft } from "../api";
import { ALL_PLATFORMS, platformMeta } from "../platforms";

const PLAN_STATUS_LABEL: Record<ContentPlan["status"], string> = {
  planned: "待执行",
  generating: "生产中",
  generated: "已生成",
  reviewed: "已审核",
  archived: "已归档",
  cancelled: "已取消",
  failed: "失败，可重试",
};

export default function Calendar() {
  const { showToast, withPending, isPending } = useApp();
  const today = new Date();
  const [cursorDate, setCursorDate] = useState(() => new Date(today));
  const [viewMode, setViewMode] = useState<"month" | "week" | "day">("week");
  const year = cursorDate.getFullYear();
  const month = cursorDate.getMonth();

  const [events, setEvents] = useState<ScheduleEvent[]>([]);
  const [platformFilter, setPlatformFilter] = useState<Platform | "all">("all");
  const [plans, setPlans] = useState<ContentPlan[]>([]);
  const [planDetail, setPlanDetail] = useState<(ContentPlan & { drafts: Array<{ draft_id: string; platform: Platform; status: string; title: string; round_id: number | null; round_number: number | null }>; rounds: Array<{ round_id: number; round_number: number | null; task_id: string | null; created_at: string }> }) | null>(null);
  const [planDetailLoading, setPlanDetailLoading] = useState(false);
  const [showArchivedPlans, setShowArchivedPlans] = useState(false);
  const [planTitle, setPlanTitle] = useState("");
  const [planTopic, setPlanTopic] = useState("");
  const [planDate, setPlanDate] = useState(_fmt(today));
  const [planCount, setPlanCount] = useState("1");
  const [planPlatforms, setPlanPlatforms] = useState<Platform[]>([]);
  const [planNotes, setPlanNotes] = useState("");
  const [planFormOpen, setPlanFormOpen] = useState(false);
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null);
  const [editingPlanVersion, setEditingPlanVersion] = useState<number | null>(null);
  const [unscheduled, setUnscheduled] = useState<UnscheduledDraft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 行内排期编辑：selected 为草稿 id，draftTime 为 datetime-local 值
  const [selected, setSelected] = useState<{ id: string; title: string } | null>(null);
  const [targetDraftId, setTargetDraftId] = useState<string | null>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("target_type") === "draft" ? params.get("target_id") : null;
  });
  const [draftTime, setDraftTime] = useState("");
  useEffect(() => {
    const syncTarget = () => {
      const params = new URLSearchParams(window.location.search);
      setTargetDraftId(params.get("target_type") === "draft" ? params.get("target_id") : null);
    };
    window.addEventListener("popstate", syncTarget);
    return () => window.removeEventListener("popstate", syncTarget);
  }, []);
  useEffect(() => {
    if (!targetDraftId) return;
    let active = true;
    void api.draftById(targetDraftId).then((draft) => {
      if (!active || !draft) return;
      const targetDate = draft.scheduled_publish_at || draft.saved_at || draft.created_at;
      const parsed = targetDate ? new Date(targetDate) : null;
      if (parsed && !Number.isNaN(parsed.getTime())) {
        setCursorDate(new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate()));
        setViewMode("day");
      }
    }).catch((error) => {
      if (active) showToast("加载通知关联草稿失败，请刷新页面重试", "error");
    });
    return () => { active = false; };
  }, [targetDraftId]);
  const [pendingDraft, setPendingDraft] = useState<Record<string, string>>({});
  const scheduleDrawerRef = useRef<HTMLElement | null>(null);
  const planDrawerRef = useRef<HTMLElement | null>(null);

  // API 查询范围与当前视图一致：月视图覆盖完整日历网格，周/日视图只请求可见日期。
  const visibleStart = useMemo(() => {
    if (viewMode === "day") return new Date(year, month, cursorDate.getDate());
    if (viewMode === "week") {
      const d = new Date(cursorDate);
      d.setDate(d.getDate() - d.getDay());
      d.setHours(0, 0, 0, 0);
      return d;
    }
    const d = new Date(year, month, 1);
    d.setDate(1 - d.getDay());
    return d;
  }, [viewMode, year, month, cursorDate]);
  const visibleEndExclusive = useMemo(() => {
    if (viewMode === "day") {
      const d = new Date(visibleStart);
      d.setDate(d.getDate() + 1);
      return d;
    }
    if (viewMode === "week") {
      const d = new Date(visibleStart);
      d.setDate(d.getDate() + 7);
      return d;
    }
    const d = new Date(year, month + 1, 1);
    d.setDate(d.getDate() + (6 - d.getDay()) + 1);
    return d;
  }, [viewMode, visibleStart, year, month]);
  const days = useMemo(() => {
    const result: { date: Date; key: string; events: ScheduleEvent[]; plans: ContentPlan[]; inMonth: boolean }[] = [];
    for (let d = new Date(visibleStart); d < visibleEndExclusive; d.setDate(d.getDate() + 1)) {
      const date = new Date(d);
      const key = _fmt(date);
      result.push({ date, key, events: events.filter((event) => _eventDate(event) === key && (platformFilter === "all" || event.platform === platformFilter)), plans: plans.filter((plan) => plan.scheduled_date === key && (platformFilter === "all" || plan.platforms.length === 0 || plan.platforms.includes(platformFilter)) && (showArchivedPlans || (plan.status !== "cancelled" && plan.status !== "archived"))), inMonth: date.getMonth() === month });
    }
    return result;
  }, [visibleStart, visibleEndExclusive, events, plans, month, showArchivedPlans, platformFilter]);

  const openPlanDetails = async (planId: string) => {
    setPlanDetailLoading(true);
    try { setPlanDetail(await api.contentPlan(planId)); }
    catch (e: any) { showToast(`加载计划详情失败：${e?.message ?? e}`, "error"); }
    finally { setPlanDetailLoading(false); }
  };

  const beginEditPlan = (plan: ContentPlan) => {
    setEditingPlanId(plan.id);
    setEditingPlanVersion(plan.version);
    setPlanTitle(plan.title);
    setPlanTopic(plan.topic ?? "");
    setPlanDate(plan.scheduled_date);
    setPlanCount(String(plan.planned_count));
    setPlanPlatforms(plan.platforms ?? []);
    setPlanNotes(plan.notes ?? "");
    setPlanDetail(null);
    setPlanFormOpen(true);
  };

  const startPlan = async (plan: ContentPlan) => {
    try {
      const result = await withPending(`content-plan:start:${plan.id}`, () => api.startContentPlan(plan.id));
      if (!result) return;
      showToast("已加入全局 FIFO 队列", "success");
      await load();
    } catch (e: any) { showToast(`启动计划失败：${e?.message ?? e}`, "error"); }
  };

  const cancelPlan = async (plan: ContentPlan) => {
    try {
      await api.cancelContentPlan(plan.id, plan.version);
      showToast("计划已取消", "success");
      await load();
    } catch (e: any) { showToast(`取消失败：${e?.message ?? e}`, "error"); }
  };

  const archivePlan = async (plan: ContentPlan) => {
    if (!window.confirm(`归档计划「${plan.title}」？生产轮次和关联草稿都会保留，可随时恢复。`)) return;
    try {
      const result = await withPending(`content-plan:archive:${plan.id}`, () => api.archiveContentPlan(plan.id, plan.version));
      if (!result) return;
      showToast("计划已归档", "success");
      setPlanDetail(null);
      await load();
    } catch (e: any) { showToast(`归档失败：${e?.message ?? e}`, "error"); }
  };

  const restorePlan = async (plan: ContentPlan) => {
    try {
      const result = await withPending(`content-plan:restore:${plan.id}`, () => api.restoreContentPlan(plan.id, plan.version));
      if (!result) return;
      showToast(`计划已恢复为「${PLAN_STATUS_LABEL[result.status]}」`, "success");
      setPlanDetail(null);
      await load();
    } catch (e: any) { showToast(`恢复失败：${e?.message ?? e}`, "error"); }
  };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, planResult] = await Promise.all([
        api.schedule(_fmt(visibleStart), _fmt(visibleEndExclusive)),
        api.contentPlans(_fmt(visibleStart), _fmt(new Date(visibleEndExclusive.getTime() - 86400000))),
      ]);
      setEvents(r.items);
      setPlans(planResult.items);
      setUnscheduled(r.unscheduled ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [viewMode, year, month, cursorDate]);
  useEffect(() => {
    if (!plans.some((plan) => plan.status === "generating")) return;
    const timer = window.setInterval(() => { void load(); }, 10000);
    return () => window.clearInterval(timer);
  }, [plans, viewMode, year, month, cursorDate]);


  const platformColors: Record<string, string> = {
    xhs: "#fe2c55", douyin: "#000", wechat_channels: "#07c160", kuaishou: "#ff6a00",
  };
  const statusLabel: Record<string, string> = {
    draft_pending: "待存草稿",
    draft_saved: "草稿已存",
    user_published: "用户确认",
    published: "已发布",
  };

  /** 保存排期：datetime-local 的值是 "YYYY-MM-DDTHH:MM"，后端按本地时间解释（R5） */
  const saveSchedule = async (draftId: string, value: string) => {
    if (!value) { showToast("请先选择排期时间", "error"); return; }
    try {
      await withPending(`schedule:${draftId}`, () => api.scheduleDraft(draftId, value));
      showToast("✓ 排期已保存（仅排期，不会触发发布）", "success");
      setSelected(null);
      setDraftTime("");
      setPendingDraft((prev) => { const n = { ...prev }; delete n[draftId]; return n; });
      await load();
    } catch (e: any) {
      showToast(`排期保存失败: ${e?.message ?? e}`, "error");
    }
  };

  const clearSchedule = async (draftId: string) => {
    try {
      await withPending(`schedule-clear:${draftId}`, () => api.updateSchedule(draftId, null));
      showToast("已取消排期", "success");
      setSelected(null);
      await load();
    } catch (e: any) {
      showToast(`取消失败: ${e?.message ?? e}`, "error");
    }
  };

  useEffect(() => {
    const drawer = selected ? scheduleDrawerRef.current : planFormOpen ? planDrawerRef.current : null;
    if (!drawer) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => Array.from(drawer.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => element.getAttribute("aria-hidden") !== "true");
    const items = focusable();
    (items[0] ?? drawer).focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (selected) { setSelected(null); setDraftTime(""); } else { setPlanFormOpen(false); setEditingPlanId(null); setEditingPlanVersion(null); }
        return;
      }
      if (event.key !== "Tab") return;
      const current = focusable();
      if (!current.length) { event.preventDefault(); drawer.focus(); return; }
      const first = current[0], last = current[current.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previousFocus?.focus(); };
  }, [selected, planFormOpen]);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">内容生产 · 排期视图</div>
          <h1 className="page-title">内容排期日历</h1>
          <div className="page-desc">
            以内容、平台和发布时间为核心查看排期；系统永不自动触发发布动作。计划归属字段接入后，将在此显示主计划、平台子计划和阶段。
            {!loading && <span className="tag teal" style={{ marginLeft: 8 }}>
              当前{viewMode === "month" ? "月" : viewMode === "week" ? "周" : "日"} {events.length} 条
            </span>}
          </div>
        </div>
        <div className="head-actions calendar-toolbar">
          <div className="segmented-control" role="group" aria-label="日历视图">
            {(["month", "week", "day"] as const).map((mode) => (
              <button key={mode} className={`btn sm ${viewMode === mode ? "primary" : ""}`} aria-pressed={viewMode === mode} onClick={() => setViewMode(mode)}>
                {mode === "month" ? "月" : mode === "week" ? "周" : "日"}
              </button>
            ))}
          </div>
          <button className="btn sm" onClick={() => setCursorDate((d) => shiftCalendarDate(d, viewMode, -1))}>上一{viewMode === "month" ? "月" : viewMode === "week" ? "周" : "日"}</button>
          <button className="btn sm" onClick={() => setCursorDate(new Date(today))}>今天</button>
          <button className="btn sm" onClick={() => setCursorDate((d) => shiftCalendarDate(d, viewMode, 1))}>下一{viewMode === "month" ? "月" : viewMode === "week" ? "周" : "日"}</button>
          <input aria-label="选择日历日期" className="field calendar-date-picker" type="date" value={_fmt(cursorDate)} onChange={(e) => { if (e.target.value) { const [y, m, d] = e.target.value.split("-").map(Number); setCursorDate(new Date(y, m - 1, d)); } }} />
          <button className="btn sm primary" onClick={() => { setPlanDate(_fmt(cursorDate)); setPlanFormOpen(true); }}>＋ 新建计划</button>
          <select className="field" aria-label="按平台筛选排期" value={platformFilter} onChange={(e) => setPlatformFilter(e.target.value as Platform | "all")} style={{ maxWidth: 150 }}>
            <option value="all">全部平台</option>
            {ALL_PLATFORMS.map((p) => <option key={p} value={p}>{platformMeta(p).label}</option>)}
          </select>
          <label className="calendar-plan-check" title="在日历中同时显示已归档和已取消的计划">
            <input type="checkbox" checked={showArchivedPlans} onChange={(e) => setShowArchivedPlans(e.target.checked)} />显示已归档
          </label>
          <button className="btn sm" onClick={load} disabled={loading}>↻ 刷新</button>
        </div>
      </div>

      {error && <div className="panel" style={{ borderColor: "var(--red)" }}>
        <div className="panel-sub" style={{ color: "var(--red)", display: "flex", gap: 10, alignItems: "center" }}>
          <span>排期接口异常：{error}</span>
          <button className="btn sm" onClick={load}>↻ 重试</button>
        </div>
      </div>}

      <div className="note" style={{ marginBottom: 12, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <span>当前排期接口提供内容计划与草稿排期，尚未返回主计划版本、平台子计划 ID、阶段 ID；因此暂不显示未经确认的关联关系。</span>
        <button className="btn sm" onClick={() => { window.location.search = "?page=plan-center"; }}>打开计划管理</button>
      </div>
      <div className="panel">
        <div className="panel-head">
          <div>
            <div className="panel-title">
              <span className="icon">◷</span>{viewMode === "month" ? `${year} 年 ${month + 1} 月` : viewMode === "week" ? `${_fmt(visibleStart)} — ${_fmt(new Date(visibleEndExclusive.getTime() - 86400000))}` : _fmt(cursorDate)} · {viewMode === "month" ? "月视图" : viewMode === "week" ? "周视图" : "日视图"}
            </div>
            <div className="panel-sub">
              月视图查看整月分布，周／日视图聚焦具体排期；点击可编辑草稿时间。排期不会触发自动发布。
            </div>
          </div>
        </div>
        {viewMode === "month" ? (
          <div className="calendar-month-scroll">
            <div className="calendar-month-grid" style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8 }}>
              {["日", "一", "二", "三", "四", "五", "六"].map((d, i) => (
                <div key={i} style={{ textAlign: "center", fontSize: 12, fontWeight: 600, color: "var(--muted)" }}>{d}</div>
              ))}
              {days.map((d) => {
                const isToday = d.date.toDateString() === today.toDateString();
                return (
                  <div key={d.key} className="calendar-month-cell" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const id = e.dataTransfer.getData("text/plain"); const event = events.find((item) => item.id === id); if (event) { const oldValue = _toLocalInput(event.scheduled || event.saved_at); const time = oldValue.includes("T") ? oldValue.slice(11, 16) : "09:00"; void saveSchedule(id, `${d.key}T${time}`); } }} style={{
                    border: "1px solid var(--line)", borderRadius: 8, padding: 6, minHeight: 76,
                    background: isToday ? "var(--blue-weak)" : "var(--surface)",
                    opacity: d.inMonth ? 1 : 0.48,
                  }}>
                    <button className="calendar-day-number" onClick={() => { setCursorDate(d.date); setViewMode("day"); }} aria-label={`查看 ${d.key} 当天内容`}>
                      {d.date.getDate()}
                    </button>
                    <button type="button" className="calendar-day-add" aria-label={`在 ${d.key} 新建计划`} onClick={() => { setPlanDate(d.key); setPlanFormOpen(true); }}>＋</button>
                    {loading && <div className="loading" style={{ marginTop: 6, fontSize: 11, color: "var(--muted)" }}><div className="spinner" />加载中...</div>}
                    {d.plans.slice(0, 1).map((plan) => (
                      <button type="button" key={plan.id} className="calendar-plan-chip" title={`${plan.title} · ${plan.planned_count} 篇/平台`} onClick={() => void openPlanDetails(plan.id)}>
                        <strong>计划</strong><span>{plan.title}</span>
                      </button>
                    ))}
                    {d.events.slice(0, 2).map((e) => {
                      const editable = e.status === "draft_pending" || e.status === "draft_saved";
                      return (
                        <button type="button" key={e.id} className="calendar-event" draggable={editable}
                          onDragStart={(event) => { event.dataTransfer.setData("text/plain", e.id); event.dataTransfer.effectAllowed = "move"; }}
                          title={editable ? `${e.title} · 点击改排期` : `${e.title} · ${statusLabel[e.status] ?? e.status}（不可改排期）`}
                          disabled={!editable}
                          onClick={() => { setSelected({ id: e.id, title: e.title }); setDraftTime(_toLocalInput(e.scheduled || e.saved_at)); }}
                          style={{ borderLeft: `3px solid ${platformColors[e.platform] || "#666"}`, outline: selected?.id === e.id || targetDraftId === e.id ? "2px solid var(--blue)" : "none" }}>
                          <span className="calendar-event-title">{e.title}</span>
                          <span className="calendar-event-meta">{e.scheduled ? e.scheduled.slice(11, 16) : "未排期"}{statusLabel[e.status] ? ` · ${statusLabel[e.status]}` : ""}</span>
                        </button>
                      );
                    })}
                    {(Math.max(0, d.plans.length - 1) + Math.max(0, d.events.length - 2) > 0) && (
                      <button type="button" className="calendar-more-link" onClick={() => { setCursorDate(d.date); setViewMode("day"); }}>
                        还有 {Math.max(0, d.plans.length - 1) + Math.max(0, d.events.length - 2)} 项 · 查看当天
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className={`calendar-agenda ${viewMode === "week" ? "calendar-week-grid" : ""}`}>
            {days.map((d) => (
              <section className="calendar-agenda-day" key={d.key} onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const id = e.dataTransfer.getData("text/plain"); const event = events.find((item) => item.id === id); if (event) { const oldValue = _toLocalInput(event.scheduled || event.saved_at); const time = oldValue.includes("T") ? oldValue.slice(11, 16) : "09:00"; void saveSchedule(id, `${d.key}T${time}`); } }}>
                <button className="calendar-agenda-date" onClick={() => { if (viewMode === "week") { setCursorDate(d.date); setViewMode("day"); } else { setPlanDate(d.key); setPlanFormOpen(true); } }}>
                  <strong>{d.date.toLocaleDateString("zh-CN", { weekday: "long" })}</strong>
                  <span>{d.key}{d.date.toDateString() === today.toDateString() ? " · 今天" : ""}</span>
                </button>
                {d.plans.map((plan) => (
                  <div className="calendar-agenda-plan" key={plan.id}>
                    <button className="calendar-plan-label" onClick={() => void openPlanDetails(plan.id)}>计划详情</button>
                    <span className="calendar-agenda-copy"><strong>{plan.title}</strong><small>{plan.planned_count} 篇/平台 · {plan.platforms.length ? plan.platforms.map((p) => platformMeta(p).label).join("、") : "使用启用平台"} · {PLAN_STATUS_LABEL[plan.status]}</small></span>
                    {(plan.status === "planned" || plan.status === "failed") && (
                      <button className="btn sm primary" disabled={isPending(`content-plan:start:${plan.id}`)} onClick={() => void startPlan(plan)}>
                        {isPending(`content-plan:start:${plan.id}`) ? "入队中…" : plan.status === "failed" ? "重试入队" : "加入全局队列"}
                      </button>
                    )}
                    {(plan.status === "planned" || plan.status === "failed" || plan.status === "generating") && (
                      <button className="btn sm" onClick={() => void cancelPlan(plan)}>取消</button>
                    )}
                  </div>
                ))}
                {loading ? <div className="loading"><div className="spinner" />加载中...</div> : d.events.length === 0 && d.plans.length === 0 ? (
                  <div className="calendar-agenda-empty">当天没有排期</div>
                ) : d.events.map((e) => {
                  const editable = e.status === "draft_pending" || e.status === "draft_saved";
                  return (
                    <button type="button" key={e.id} className="calendar-agenda-event" disabled={!editable} draggable={editable}
                      onDragStart={(event) => { event.dataTransfer.setData("text/plain", e.id); event.dataTransfer.effectAllowed = "move"; }}
                      onClick={() => { setSelected({ id: e.id, title: e.title }); setDraftTime(_toLocalInput(e.scheduled || e.saved_at)); }}>
                      <span className="calendar-agenda-time">{e.scheduled ? e.scheduled.slice(11, 16) : "—"}</span>
                      <span className="calendar-agenda-color" style={{ background: platformColors[e.platform] || "#666" }} />
                      <span className="calendar-agenda-copy"><strong>{e.title}</strong><small>{platformMeta(e.platform).label} · {statusLabel[e.status] ?? e.status}</small></span>
                      <span className="calendar-agenda-action">{editable ? "编辑排期" : "不可编辑"}</span>
                    </button>
                  );
                })}
              </section>
            ))}
          </div>
        )}

        {selected && (
          <div className="calendar-drawer-backdrop" role="presentation" onClick={() => { setSelected(null); setDraftTime(""); }}>
          <section ref={scheduleDrawerRef} tabIndex={-1} className="panel calendar-side-drawer" role="dialog" aria-modal="true" aria-label="排期编辑" onClick={(e) => e.stopPropagation()}>
            <div className="panel-head">
              <div>
                <div className="panel-title"><span className="icon">✎</span>排期编辑 · {selected.title}</div>
                <div className="panel-sub">仅保存计划发布时间，不会自动触发平台发布</div>
              </div>
              <div className="head-actions" style={{ display: "flex", gap: 8 }}>
                <input
                  className="field"
                  type="datetime-local"
                  value={draftTime}
                  onChange={(e) => {
                    setDraftTime(e.target.value);
                    setPendingDraft((p) => ({ ...p, [selected.id]: e.target.value }));
                  }}
                  style={{ width: 220 }}
                />
                <button
                  className="btn primary sm"
                  disabled={isPending(`schedule:${selected.id}`)}
                  onClick={() => saveSchedule(selected.id, draftTime)}
                >
                  {isPending(`schedule:${selected.id}`) ? "保存中…" : "保存排期"}
                </button>
                <button
                  className="btn sm"
                  disabled={isPending(`schedule-clear:${selected.id}`)}
                  onClick={() => clearSchedule(selected.id)}
                >
                  取消排期
                </button>
                <button className="btn sm" onClick={() => { setSelected(null); setDraftTime(""); }}>
                  关闭
                </button>
              </div>
            </div>
          </section>
          </div>
        )}
      </div>

      {planDetail && (
        <div className="calendar-plan-modal" role="dialog" aria-modal="true" aria-label="内容计划详情" onClick={() => setPlanDetail(null)}>
          <section className="panel" onClick={(e) => e.stopPropagation()}>
            <div className="panel-head">
              <div><div className="panel-title">{planDetail.title}</div><div className="panel-sub">{planDetail.scheduled_date} · {planDetail.planned_count} 篇/平台 · {PLAN_STATUS_LABEL[planDetail.status]}</div></div>
              <div style={{ display: "flex", gap: 8 }}>
                {(planDetail.status === "planned" || planDetail.status === "failed") && <button className="btn sm primary" onClick={() => beginEditPlan(planDetail)}>编辑计划</button>}
                {(["generated", "reviewed", "failed", "cancelled"] as ContentPlan["status"][]).includes(planDetail.status) && (
                  <button className="btn sm" disabled={isPending(`content-plan:archive:${planDetail.id}`)} onClick={() => void archivePlan(planDetail)}>
                    {isPending(`content-plan:archive:${planDetail.id}`) ? "归档中…" : "归档"}
                  </button>
                )}
                {(planDetail.status === "archived" || planDetail.status === "cancelled") && (
                  <button className="btn sm" disabled={isPending(`content-plan:restore:${planDetail.id}`)} onClick={() => void restorePlan(planDetail)}>
                    {isPending(`content-plan:restore:${planDetail.id}`) ? "恢复中…" : "恢复"}
                  </button>
                )}
                <button className="btn sm" onClick={() => setPlanDetail(null)}>关闭</button>
              </div>
            </div>
            {planDetail.topic && <div className="note">{planDetail.topic}</div>}
            {planDetail.notes && <div className="note">备注：{planDetail.notes}</div>}
            <div className="note" style={{ marginBottom: 12 }}><strong>计划归属</strong><div>主计划版本：待接入关联字段</div><div>平台子计划：待接入关联字段</div><div>所属阶段：待接入关联字段</div></div>
            <div className="calendar-plan-detail-grid">
              <div><strong>生产轮次（{planDetail.rounds.length}）</strong>
                {planDetail.rounds.length ? planDetail.rounds.map((round) => <div className="calendar-plan-detail-row" key={round.round_id}>{round.round_number != null ? `第 ${round.round_number} 轮` : "轮次信息不可用"} · {round.task_id ? `任务 ${round.task_id.slice(0, 8)}` : "尚未入队"}</div>) : <div className="note">尚未启动生产</div>}
              </div>
              <div><strong>关联草稿（{planDetail.drafts.length}）</strong>
                {planDetail.drafts.length ? planDetail.drafts.map((draft) => <div className="calendar-plan-detail-row" key={draft.draft_id}>{draft.title || draft.draft_id} · {platformMeta(draft.platform).label} · {draft.status}</div>) : <div className="note">生产完成后自动回写草稿</div>}
              </div>
            </div>
            {planDetailLoading && <div className="loading"><div className="spinner" />刷新中…</div>}
          </section>
        </div>
      )}

      {planFormOpen && <div className="calendar-drawer-backdrop" role="presentation" onClick={() => { setPlanFormOpen(false); setEditingPlanId(null); setEditingPlanVersion(null); }}>
      <section ref={planDrawerRef} tabIndex={-1} className="panel calendar-side-drawer calendar-plan-create" role="dialog" aria-modal="true" aria-label={editingPlanId ? "编辑内容计划" : "新建内容计划"} onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div><div className="panel-title">{editingPlanId ? "编辑内容计划" : "新建内容计划"}</div><div className="panel-sub">{editingPlanId ? "编辑仅影响后续执行，不会回写已经生成的历史草稿。" : "创建计划不会自动开始生产；加入全局队列后才会生成，发布仍需人工确认。"}</div></div>
          <button className="btn sm" onClick={() => { setPlanFormOpen(false); setEditingPlanId(null); setEditingPlanVersion(null); }}>关闭</button>
        </div>
        <div className="calendar-plan-form">
          <label>计划名称<input className="field" value={planTitle} maxLength={160} onChange={(e) => setPlanTitle(e.target.value)} placeholder="例如：本周产品功能系列" /></label>
          <label>选题方向备注（非过滤条件）<input className="field" value={planTopic} maxLength={500} onChange={(e) => setPlanTopic(e.target.value)} placeholder="选题方向备注；生产时仍从选题池取题" /></label>
          <label>计划日期<input className="field" type="date" value={planDate} onChange={(e) => setPlanDate(e.target.value)} /></label>
          <label>每个平台篇数<input className="field" type="number" min="1" max="50" value={planCount} onChange={(e) => setPlanCount(e.target.value)} /></label>
          <label className="calendar-plan-platforms">目标平台
            <span>{(["xhs", "douyin", "wechat_channels", "kuaishou"] as Platform[]).map((platform) => (
              <label key={platform} className="calendar-plan-check"><input type="checkbox" checked={planPlatforms.includes(platform)} onChange={(e) => setPlanPlatforms((old) => e.target.checked ? [...old, platform] : old.filter((p) => p !== platform))} />{platformMeta(platform).label}</label>
            ))}</span>
          </label>
          <label className="calendar-plan-notes">备注<textarea className="field" rows={2} value={planNotes} maxLength={4000} onChange={(e) => setPlanNotes(e.target.value)} placeholder="可选" /></label>
          <button className="btn primary" disabled={!planTitle.trim() || !planDate || Number(planCount) < 1 || Number(planCount) > 100 || isPending(editingPlanId ? `content-plan:update:${editingPlanId}` : "content-plan:create")} onClick={async () => {
            const payload = { title: planTitle.trim(), topic: planTopic.trim(), platforms: planPlatforms, planned_count: Number(planCount), scheduled_date: planDate, notes: planNotes.trim() };
            try {
              if (editingPlanId && editingPlanVersion != null) {
                await withPending(`content-plan:update:${editingPlanId}`, () => api.updateContentPlan(editingPlanId, { ...payload, version: editingPlanVersion }));
                showToast("内容计划已更新", "success");
              } else {
                await withPending("content-plan:create", () => api.createContentPlan(payload));
                showToast("内容计划已创建", "success");
              }
              setPlanTitle(""); setPlanTopic(""); setPlanCount("1"); setPlanPlatforms([]); setPlanNotes("");
              setPlanFormOpen(false); setEditingPlanId(null); setEditingPlanVersion(null);
              setCursorDate(new Date(`${planDate}T12:00:00`));
              await load();
            } catch (e: any) { showToast(`${editingPlanId ? "更新" : "创建"}计划失败：${e?.message ?? e}`, "error"); }
          }}>{isPending(editingPlanId ? `content-plan:update:${editingPlanId}` : "content-plan:create") ? "保存中…" : editingPlanId ? "保存修改" : "创建计划"}</button>
        </div>
      </section></div>}

      {/* 未排期草稿：直接挑选时间写入排期 */}
      <div className="panel">
        <div className="panel-head">
          <div>
            <div className="panel-title"><span className="icon">◷</span>未排期草稿 · {unscheduled.length} 篇</div>
            <div className="panel-sub">还没有发布时间的草稿</div>
          </div>
        </div>
        {unscheduled.length === 0 ? (
          <EmptyHint next="到「内容生产 → 生产线」运行一轮并存草稿，草稿会出现在这里">当前没有待排期草稿</EmptyHint>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>类型</th><th>标题</th><th>平台</th><th>状态</th><th>排期时间</th><th>操作</th></tr>
              </thead>
              <tbody>
                {unscheduled.filter((d) => platformFilter === "all" || d.platform === platformFilter).map((d) => (
                  <tr key={d.id} style={targetDraftId === d.id ? { background: "var(--surface-2)", outline: "2px solid var(--blue)" } : undefined}>
                    <td style={{ fontSize: 11, color: "var(--subtle)" }}>草稿</td>
                    <td>{d.title ?? <span style={{ color: "var(--subtle)" }}>—</span>}</td>
                    <td>
                      <span className="tag" style={{ background: platformColors[d.platform], color: "#fff" }}>
                        {platformMeta(d.platform).label}
                      </span>
                    </td>
                    <td>{statusLabel[d.status] ?? d.status}</td>
                    <td>
                      <input
                        className="field"
                        type="datetime-local"
                        value={pendingDraft[d.id] ?? ""}
                        onChange={(e) => setPendingDraft((p) => ({ ...p, [d.id]: e.target.value }))}
                        style={{ width: 200 }}
                      />
                    </td>
                    <td>
                      <button
                        className="btn primary sm"
                        disabled={isPending(`schedule:${d.id}`)}
                        onClick={() => saveSchedule(d.id, pendingDraft[d.id] ?? "")}
                      >
                        {isPending(`schedule:${d.id}`) ? "保存中…" : "排期"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function shiftCalendarDate(date: Date, mode: "month" | "week" | "day", direction: number): Date {
  if (mode === "month") return new Date(date.getFullYear(), date.getMonth() + direction, 1);
  const next = new Date(date);
  next.setDate(next.getDate() + (mode === "week" ? 7 : 1) * direction);
  return next;
}

function _fmt(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

function _eventDate(e: ScheduleEvent): string {
  const raw = e.scheduled || e.saved_at || "";
  // ISO 含时间部分 —— 只取日期
  return raw.slice(0, 10);
}

/** ISO / 日期串 → datetime-local 需要的 "YYYY-MM-DDTHH:MM"；无时间部分补 09:00 */
function _toLocalInput(raw: string | null): string {
  if (!raw) return "";
  const text = raw.replace(" ", "T");
  if (text.length >= 16) return text.slice(0, 16);
  return `${text.slice(0, 10)}T09:00`;
}
