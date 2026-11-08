import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const calendarUrl = new URL("../src/pages/Calendar.tsx", import.meta.url);

test("calendar plan details never use an internal draft ID as visible title fallback", async () => {
  const source = await readFile(calendarUrl, "utf8");
  assert.match(source, /draft\.title \|\| "未命名草稿"/,
    "missing draft titles must use a business-readable fallback");
  assert.doesNotMatch(source, /draft\.title \|\| draft\.draft_id/,
    "internal draft IDs must not be rendered as ordinary titles");
  assert.match(source, /key=\{draft\.draft_id\}/,
    "the internal ID may remain as a non-visible React key");
  assert.doesNotMatch(source, /batch\.round_number \?\? batch\.round_id/,
    "round IDs must not be used as visible round-number fallbacks");
  assert.doesNotMatch(source, /batchDetail\.batch\.round_number \?\? batchDetail\.batch\.round_id/,
    "batch detail headings must not expose internal round IDs");
  assert.doesNotMatch(source, /batchDetail\.batch\.master_plan_version_id \?\?/,
    "master plan version IDs must not be displayed");
  assert.doesNotMatch(source, /batchDetail\.batch\.sub_plan_version_id \?\?/,
    "sub-plan version IDs must not be displayed");
  assert.match(source, /master_plan_version_id != null \? "已关联"/,
    "master plan association should use a business-readable status");
  assert.match(source, /sub_plan_version_id != null \? "已关联"/,
    "sub-plan association should use a business-readable status");
});
