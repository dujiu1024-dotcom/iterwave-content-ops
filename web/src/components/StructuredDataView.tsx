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

const TECHNICAL_KEY_PATTERN = /(^|_)(id|ids|tenant|worker|trace|request|execution|correlation|fingerprint|signature|raw|payload|error_code|stack|debug)(_|$)/i;
const SENSITIVE_KEY_PATTERN = /(^|_)(secret|token|password|passwd|authorization|cookie|api_key|credential|private_key)(_|$)/i;

function normalizedKey(key: string): string {
  return key.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[-\s]/g, "_").toLowerCase();
}

function isTechnicalKey(key: string): boolean {
  return TECHNICAL_KEY_PATTERN.test(normalizedKey(key));
}

function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEY_PATTERN.test(normalizedKey(key));
}

function labelFor(key: string): string {
  const normalized = normalizedKey(key);
  return LABELS[normalized] ?? normalized.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function collectTechnical(value: unknown, path = ""): Array<{ path: string; value: unknown }> {
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => collectTechnical(item, path ? `${path}[${index}]` : `[${index}]`));
  }
  if (!value || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => {
    if (isSensitiveKey(key)) return [];
    const currentPath = path ? `${path}.${key}` : key;
    if (isTechnicalKey(key)) return [{ path: currentPath, value: item }];
    return collectTechnical(item, currentPath);
  });
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
      !isTechnicalKey(key) && !isSensitiveKey(key) && item !== undefined && item !== null && item !== ""
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
  const technical = collectTechnical(data);
  return <section className="structured-data-view">
    {title && <h4 className="structured-data-title">{title}</h4>}
    <Value value={data} />
    {technical.length > 0 && <details className="structured-data-diagnostics">
      <summary>高级诊断信息（{technical.length} 项）</summary>
      <dl className="structured-data-grid">{technical.map(({ path, value }) =>
        <div className="structured-data-field" key={path}>
          <dt>{path.split(".").map((part) => labelFor(part)).join(" / ")}</dt>
          <dd><code>{typeof value === "string" ? value : JSON.stringify(value)}</code></dd>
        </div>
      )}</dl>
    </details>}
  </section>;
}
