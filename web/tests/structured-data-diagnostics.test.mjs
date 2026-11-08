import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const componentUrl = new URL("../src/components/StructuredDataView.tsx", import.meta.url);
const stylesUrl = new URL("../src/styles.css", import.meta.url);

test("structured diagnostics stay opt-in and preserve the business-first view", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /function collectTechnical\(/, "technical fields must be collected separately");
  assert.match(source, /function sanitizeDiagnosticValue\(/, "diagnostic values must be recursively sanitized");
  assert.match(source, /\.filter\(\(\[key\]\) => !isSensitiveKey\(key\)\)/,
    "sensitive keys must be removed recursively");
  assert.match(source, /<details className="structured-data-diagnostics">/,
    "diagnostics must use a native disclosure element");
  assert.match(source, /<summary>高级诊断信息/,
    "diagnostics must have an explicit user-facing disclosure label");
  assert.doesNotMatch(source, /<details[^>]*\bopen\b/,
    "diagnostics must be collapsed by default");
  assert.match(source, /<Value value=\{data\} \/>/,
    "the existing business-facing structured view must remain present");
});

test("diagnostic output wraps long values and can shrink on narrow screens", async () => {
  const source = await readFile(stylesUrl, "utf8");
  const start = source.indexOf(".structured-data-diagnostics {");
  assert.ok(start >= 0, "diagnostics styles must exist");
  const rules = source.slice(start, source.indexOf("}", start) + 1);
  assert.match(rules, /min-width:\s*0/);
  const codeStart = source.indexOf(".structured-data-diagnostics code {");
  assert.ok(codeStart >= 0, "diagnostic code styles must exist");
  const codeRules = source.slice(codeStart, source.indexOf("}", codeStart) + 1);
  assert.match(codeRules, /overflow-wrap:\s*anywhere/);
  assert.match(codeRules, /white-space:\s*pre-wrap/);
});
