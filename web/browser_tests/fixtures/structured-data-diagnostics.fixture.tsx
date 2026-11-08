import React from "react";
import { createRoot } from "react-dom/client";
import StructuredDataView from "../../src/components/StructuredDataView";
import "../../src/styles.css";

const longDiagnostic = "diagnostic-segment-".repeat(45);
const data = {
  summary: "模拟经营资料，用于验证业务优先展示",
  business_name: "浏览器验收样本",
  trace_id: "trace-visible-2026-qa",
  worker_id: "worker-visible-42",
  raw_payload: {
    safe_detail: longDiagnostic,
    password: "NEVER_RENDER_PASSWORD_91",
    nested: [{ api_key: "NEVER_RENDER_API_KEY_72", safe: "visible-safe-detail" }],
  },
  serialized_payload: JSON.stringify({
    request_id: "request-visible-31",
    credentials: { access_token: "NEVER_RENDER_TOKEN_53" },
    nested: [{ private_key: "NEVER_RENDER_PRIVATE_KEY_18", safe: "serialized-safe-detail" }],
  }),
};

createRoot(document.getElementById("root")!).render(
  <StructuredDataView data={data} title="结构化业务数据验收" />,
);
