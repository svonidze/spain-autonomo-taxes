"use strict";

// Behavior checks for the shared UI state helpers in app.js: the loading
// skeleton, the error state with retry/reload affordances, and contextual
// empty rows. Extraction mirrors tests/test_web_ui_charts.js: pure helpers
// are pulled out by name and executed with a stubbed identity i18n.

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appPath = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js");
const appSource = fs.readFileSync(appPath, "utf8");

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`Could not find ${name} in app.js`);
  const signatureEnd = source.indexOf(")", start);
  const braceIndex = source.indexOf("{", signatureEnd);
  let depth = 0;
  for (let index = braceIndex; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Could not extract ${name} from app.js`);
}

const context = vm.createContext({
  t: (key) => key,
});
for (const name of ["escapeHtml", "emptyRow", "errorState", "uiLoadingSkeleton"]) {
  vm.runInContext(extractFunction(appSource, name), context);
}

// The loading skeleton keeps an accessible text and hides the shimmer bars
// from assistive tech.
{
  const html = vm.runInContext("uiLoadingSkeleton()", context);
  assert.ok(html.includes('class="loading-state state-loading"'));
  assert.ok(html.includes('aria-hidden="true"'));
  assert.strictEqual((html.match(/skeleton-line/g) || []).length, 3);
  assert.ok(html.includes("common.loading"));
}

// A generic failure renders the distinct error look with a retry button and
// keeps the hostile message inert.
{
  const html = vm.runInContext(
    'errorState({message: "<img src=x onerror=alert(1)>", code: null})',
    context
  );
  assert.ok(html.includes("state-error"));
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes("data-retry-view"));
  assert.ok(html.includes("common.loadFailed"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
}

// An expired session offers a reload instead of a retry.
{
  const html = vm.runInContext(
    'errorState({message: "forbidden", code: "session_forbidden"})',
    context
  );
  assert.ok(html.includes("data-reload-view"));
  assert.ok(!html.includes("data-retry-view"));
  assert.ok(html.includes("common.sessionExpired"));
}

// Empty rows accept a contextual message, escape it, and fall back to the
// shared no-records label.
{
  const html = vm.runInContext('emptyRow(6, "<b>evil</b>")', context);
  assert.ok(html.includes('colspan="6"'));
  assert.ok(html.includes("&lt;b&gt;evil&lt;/b&gt;"));
  assert.ok(!html.includes("<b>evil"));
  const fallback = vm.runInContext("emptyRow(7)", context);
  assert.ok(fallback.includes('colspan="7"'));
  assert.ok(fallback.includes("common.noRecords"));
}

console.log(JSON.stringify({ok: true}));
