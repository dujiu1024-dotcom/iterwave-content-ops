import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("legacy production plan tabs are labeled and point to canonical plan management", async () => {
  const source = await readFile(
    new URL("../src/pages/Production.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /label: "旧版总计划参数"/);
  assert.match(source, /label: "旧版平台分项参数"/);
  assert.match(source, /旧版计划参数兼容入口[\s\S]*?href="\?page=plan-center"/);
  assert.match(source, /旧版平台分项参数仅用于兼容既有配置[\s\S]*?href="\?page=plan-center"/);
});
