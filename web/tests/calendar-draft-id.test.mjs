import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const calendarUrl = new URL("../src/pages/Calendar.tsx", import.meta.url);

test("calendar plan details never use an internal draft ID as visible title fallback", async () => {
  const source = await readFile(calendarUrl, "utf8");
  assert.match(source, /draft\\.title \\|\\| "未命名草稿"/,
    "missing draft titles must use a business-readable fallback");
  assert.doesNotMatch(source, /draft\\.title \\|\\| draft\\.draft_id/,
    "internal draft IDs must not be rendered as ordinary titles");
  assert.match(source, /key=\\{draft\\.draft_id\\}/,
    "the internal ID may remain as a non-visible React key");
});
