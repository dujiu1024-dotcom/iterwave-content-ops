import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssFiles = ["../src/demo-base.css", "../src/styles.css"];

function assertBalancedCss(source, label = "CSS") {
  const pairs = { "{": "}", "(": ")", "[": "]" };
  const closing = new Set(Object.values(pairs));
  const stack = [];
  let quote = null;
  let inComment = false;
  let line = 1;

  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    const next = source[i + 1];

    if (char === "\n") line += 1;

    if (inComment) {
      if (char === "*" && next === "/") {
        inComment = false;
        i += 1;
      }
      continue;
    }

    if (quote) {
      if (char === "\\") {
        i += 1;
        if (source[i] === "\n") line += 1;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }

    if (char === "/" && next === "*") {
      inComment = true;
      i += 1;
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      continue;
    }
    if (char === "\\") {
      i += 1;
      if (source[i] === "\n") line += 1;
      continue;
    }

    if (Object.hasOwn(pairs, char)) {
      stack.push({ char, line });
    } else if (closing.has(char)) {
      const opening = stack.pop();
      assert.ok(
        opening && pairs[opening.char] === char,
        `${label} has unexpected "${char}" at line ${line}`,
      );
    }
  }

  assert.equal(inComment, false, `${label} has an unterminated comment`);
  assert.equal(quote, null, `${label} has an unterminated string`);
  assert.deepEqual(
    stack,
    [],
    `${label} has unclosed "${stack.at(-1)?.char ?? ""}" from line ${stack.at(-1)?.line ?? "?"}`,
  );
}

test("application stylesheets have balanced CSS blocks, functions, and selectors", async () => {
  for (const file of cssFiles) {
    const source = await readFile(new URL(file, import.meta.url), "utf8");
    assertBalancedCss(source, file);
  }
});

test("CSS structure check ignores delimiters inside comments and strings", () => {
  assert.doesNotThrow(() => assertBalancedCss(`
    /* } ) ] */
    .example::before { content: "{ not a block }"; }
    @media (max-width: 600px) { .example[data-value="]"] { padding: 4px; } }
  `));
});

test("CSS structure check catches a missing root block terminator", () => {
  assert.throws(
    () => assertBalancedCss(":root { --token: 12px;\n* { box-sizing: border-box; }"),
    /unclosed/,
  );
});
