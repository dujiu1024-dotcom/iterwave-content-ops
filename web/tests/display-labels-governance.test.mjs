import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import test from "node:test";

const sourceRoot = new URL("../src/", import.meta.url);
const labelsUrl = new URL("../src/displayLabels.ts", import.meta.url);
const enumProperties = [
  "status", "state", "source", "platform", "verdict", "decision",
  "outcome", "severity", "stage", "phase", "action", "type",
  "event_type", "guard_outcome", "draft_status", "round_status", "task_type",
];
const enumPropertyPattern = enumProperties.join("|");
const memberPath = "[A-Za-z_$][\\w$]*(?:(?:\\.|\\?\\.)[A-Za-z_$][\\w$]*)*";
const directEnumInterpolation = new RegExp("\\{\\s*" + memberPath + "\\.(?:" + enumPropertyPattern + ")\\s*\\}", "g");
const templateEnumInterpolation = new RegExp("\\$\\{\\s*" + memberPath + "\\.(?:" + enumPropertyPattern + ")\\s*\\}", "g");

async function collectTsxFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await collectTsxFiles(path));
    } else if (/\.(?:tsx|jsx)$/.test(entry.name)) {
      files.push(path);
    }
  }
  return files;
}

test("shared display-label map covers core business enums with Chinese labels", async () => {
  const source = await readFile(labelsUrl, "utf8");
  const required = [
    "pending", "queued", "running", "success", "pass", "warn", "fail",
    "error", "approved", "rejected", "draft", "active", "resolved",
    "scheduled", "processing", "delivered", "review_pending", "draft_pending",
    "in_progress", "retrying", "failed", "interrupted", "skipped",
    "cancelled", "completed", "producing", "awaiting_publish", "paused",
    "agent", "manual", "generated", "imported", "platform_readback",
    "platform_api", "critical", "warning", "info",
  ];

  for (const key of required) {
    const escaped = key.replace(/[.*+?^$()|[\]\\]/g, "\\$&");
    const entry = new RegExp("(?:^|[,\\n])\\s*(?:" + escaped + "|\\\"" + escaped + "\\\")\\s*:\\s*\\\"[^\\\"]*[\\u3400-\\u9fff][^\\\"]*\\\"", "m");
    assert.match(source, entry, "displayLabels.ts must define a Chinese label for " + key);
  }
});

test("display helpers use Chinese fallbacks instead of exposing unknown enum codes", async () => {
  const source = await readFile(labelsUrl, "utf8");
  assert.match(source, /return LABELS\[value\] \?\? fallback/);
  assert.match(source, /return displayCodeLabel\(value, "其他状态（需确认）"\)/);
  assert.match(source, /LABELS\[code\] \?\? "其他状态"/);
  assert.match(source, /return displayCodeLabel\(value, "其他来源（需确认）"\)/);
  assert.match(source, /return displayCodeLabel\(value, "其他平台（需确认）"\)/);
});

test("TSX views do not interpolate backend enum properties directly into user-visible JSX", async () => {
  const files = await collectTsxFiles(sourceRoot);
  const violations = [];

  for (const file of files) {
    const source = await readFile(file, "utf8");
    for (const pattern of [directEnumInterpolation, templateEnumInterpolation]) {
      pattern.lastIndex = 0;
      for (const match of source.matchAll(pattern)) {
        const line = source.slice(0, match.index).split("\n").length;
        violations.push(relative(new URL(".", import.meta.url).pathname, file) + ":" + line + ": " + match[0]);
      }
    }
  }

  assert.deepEqual(
    violations,
    [],
    "Use displayStatusLabel/displayCodeLabel or a domain-specific Chinese label before rendering enum fields:\n" + violations.join("\n"),
  );
});
