import type { TaskProgress } from "../api";

const STEP_ICON: Record<string, string> = {
  pending: "○", running: "◷", done: "✓", failed: "✕", skipped: "–",
};

const STEP_COLOR: Record<string, string> = {
  pending: "var(--muted)", running: "#2563eb", done: "#16a34a",
  failed: "#dc2626", skipped: "var(--muted)",
};

function formatElapsed(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return minutes ? `${minutes} 分 ${total % 60} 秒` : `${total} 秒`;
}

/** 任务实时进度：当前步骤 + 步骤清单 + 进度条 + 已用时长，避免长任务只有一句“运行中”。 */
export default function TaskProgressPanel({
  progress, elapsedSeconds, queued = false,
}: {
  progress: TaskProgress | null;
  elapsedSeconds: number | null;
  queued?: boolean;
}) {
  const steps = progress?.steps ?? [];
  const current = steps.find((step) => step.id === progress?.current_step);
  const headline = queued
    ? "排队中，等待前一个任务结束"
    : current?.label || progress?.current_label || "正在准备工作";
  const elapsed = formatElapsed(elapsedSeconds);

  return (
    <div className="note" style={{ marginTop: 10, padding: 10, border: "1px solid var(--line)", borderRadius: 9 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <strong>◷ {headline}</strong>
        {elapsed && <span className="tiny muted">已用 {elapsed}</span>}
      </div>
      {progress && steps.length > 0 && (
        <div style={{ margin: "8px 0", height: 6, background: "var(--line)", borderRadius: 3 }}>
          <div style={{
            width: `${Math.min(100, Math.max(0, progress.percent))}%`,
            height: "100%", background: "#2563eb", borderRadius: 3, transition: "width .3s",
          }} />
        </div>
      )}
      {progress?.detail && <div className="tiny" style={{ marginBottom: 6 }}>{progress.detail}</div>}
      {steps.length > 0 && (
        <div style={{ display: "grid", gap: 4 }}>
          {steps.map((step) => (
            <div key={step.id} className="tiny" style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span style={{ color: STEP_COLOR[step.status] ?? "var(--muted)" }}>{STEP_ICON[step.status] ?? "○"}</span>
              <span style={{ color: step.status === "pending" ? "var(--muted)" : undefined }}>{step.label}</span>
              {step.detail && <span className="muted" style={{ marginLeft: "auto" }}>{step.detail}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
