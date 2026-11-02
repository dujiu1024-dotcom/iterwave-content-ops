import assert from "node:assert/strict";
import test from "node:test";

import { clientErrorMessage } from "../src/api-error.js";

test("preserves safe business messages for both client and server errors", () => {
  assert.equal(clientErrorMessage(422, "标题不能为空"), "标题不能为空");
  assert.equal(clientErrorMessage(503, "图片生成服务暂时不可用，请稍后重试"), "图片生成服务暂时不可用，请稍后重试");
});

test("hides stack traces, source paths, database errors and credentials", () => {
  const generic = "服务暂时不可用，请稍后重试";
  for (const detail of [
    "Traceback (most recent call last):",
    'File "/srv/app/src/main.py", line 12',
    "sqlite3.OperationalError: database is locked",
    "Authorization: Bearer abcdefghijklmnop",
    "OPENAI_API_KEY=super-secret-value",
    "<html><body>gateway error</body></html>",
  ]) {
    assert.equal(clientErrorMessage(500, detail), generic, detail);
  }
});

test("uses stable fallbacks for empty and non-JSON details", () => {
  assert.equal(clientErrorMessage(503, "  "), "服务暂时不可用，请稍后重试");
  assert.equal(clientErrorMessage(400, undefined), "请求失败，请稍后重试");
  assert.equal(
    clientErrorMessage(400, "database is locked"),
    "操作失败，内部诊断信息已记录，请检查配置或联系管理员",
  );
});
