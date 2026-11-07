import type { ReactNode } from "react";

const LABELS: Record<string, string> = {
  summary: "摘要", description: "说明", title: "标题", name: "名称",
  status: "状态", reason: "原因", detail: "详情", category: "类别",
  source: "来源", evidence: "依据", impact: "影响", severity: "严重程度",
  recommendation: "建议", action: "建议操作", next_step: "下一步",
  risk: "风险", risks: "风险事项", assumption: "前提假设",
  assumptions: "前提假设", resource: "资源", resource_basis: "资源依据",
  pending_confirmations: "待确认事项", confirmed: "已确认", total: "总计",
  platform: "平台", type: "类型", value: "数值", count: "数量",
  date: "日期", created_at: "创建时间", updated_at: "更新时间",
  generated_at: "生成时间", model: "生成模型", version: "版本",
  field: "字段", message: "说明", code: "代码",
};
const HIDDEN_KEYS = /(^|_)(id|ids|tenant|worker|trace|request|execution|correlation|fingerprint|signature|raw|payload|error_code|stack|debug)(_|$)/i;
const TECHNICAL_KEYS = /(^|_)(id|ids|tenant|worker|trace|request|execution|correlation|fingerprint|signature|raw|payload|error_code|stack|debug)(_|$)/i;

function labelFor(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function Value({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (value == null || value === "") return <span className="structured-data-empty">暂无</span>;
  if (typeof value === "boolean") return <span>{value ? "是" : "否"}</span>;
  if (typeof value === "number" || typeof value === "string") return <span className="structured-data-value">{String(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="structured-data-empty">暂无记录</span>;
    return <div className="structured-data-list">{value.map((item, index) => (
      <div className="structured-data-item" key={index}><Value value={item} depth={depth + 1} /></div>
    ))}</div>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(([key, item]) =>
      !HIDDEN_KEYS.test(key) && item !== undefined && item !== null && item !== ""
    );
    if (!entries.length) return <span className="structured-data-empty">暂无可展示信息</span>;
    return <dl className={depth ? "structured-data-nested" : "structured-data-grid"}>
      {entries.map(([key, item]) => <div className="structured-data-field" key={key}>
        <dt>{labelFor(key)}</dt><dd><Value value={item} depth={depth + 1} /></dd>
      </div>)}
    </dl>;
  }
  return <span>{String(value)}</span>;
}

export default function StructuredDataView({ data, title }: { data: unknown; title?: string }) {
  const technical = data && typeof data === "object" && !Array.isArray(data)
    ? Object.entries(data as Record<string, unknown>).filter(([key]) => TECHNICAL_KEYS.test(key))
    : [];
  return <section className="structured-data-view">
    {title && <h4 className="structured-data-title">{title}</h4>}
    <Value value={data} />
    {technical.length > 0 && <details className="structured-data-diagnostics">
      <summary>高级诊断信息（{technical.length} 项）</summary>
      <dl className="structured-data-grid">{technical.map(([key, value]) =>
        <div className="structured-data-field" key={key}><dt>{labelFor(key)}</dt><dd><code>{typeof value === "string" ? value : JSON.stringify(value)}</code></dd></div>
      )}</dl>
    </details>}
  </section>;
}
