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
  t: (key, vars) => (vars && vars.amount !== undefined ? `${key}:${vars.amount}` : key),
  statusLabel: (value) => `label:${value}`,
  eur: (value) => `€${value.toFixed(2)}`,
});
const tonesMatch = appSource.match(/const REVIEW_CATEGORY_TONES = \{[^}]+\};/);
if (!tonesMatch) throw new Error("Could not find REVIEW_CATEGORY_TONES in app.js");
vm.runInContext(tonesMatch[0], context);
for (const name of [
  "escapeHtml",
  "emptyRow",
  "errorState",
  "uiLoadingSkeleton",
  "reviewCategoryBadge",
  "minorUnitEurPreviewText",
  "minorUnitEurPreview",
]) {
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

// The review posting-readiness chip always carries a localized label and a
// tone class; unexpected categories degrade to the neutral tone with the
// generic status label instead of leaking a raw token.
{
  const ready = vm.runInContext('reviewCategoryBadge("ready")', context);
  assert.ok(ready.includes("status-positive"));
  assert.ok(ready.includes("review.category.ready"));
  const later = vm.runInContext('reviewCategoryBadge("later")', context);
  assert.ok(later.includes("status-pending"));
  assert.ok(later.includes("review.category.later"));
  const blocked = vm.runInContext('reviewCategoryBadge("blocked")', context);
  assert.ok(blocked.includes("status-attention"));
  const unknown = vm.runInContext('reviewCategoryBadge("mystery")', context);
  assert.ok(unknown.includes("status-neutral"));
  assert.ok(unknown.includes("label:mystery"));
  const missing = vm.runInContext("reviewCategoryBadge(undefined)", context);
  assert.ok(missing.includes("status-neutral"));
  assert.ok(missing.includes("label:unknown"));
}

// Minor-unit inputs render a live euro preview: cents divide by 100, blank
// and non-numeric input produce no preview text, and zero stays a preview
// (a known zero is not missing data).
{
  assert.equal(
    vm.runInContext('minorUnitEurPreviewText("12345")', context),
    "review.irpfPreview:€123.45"
  );
  assert.equal(vm.runInContext('minorUnitEurPreviewText("0")', context), "review.irpfPreview:€0.00");
  assert.equal(vm.runInContext('minorUnitEurPreviewText("")', context), "");
  assert.equal(vm.runInContext("minorUnitEurPreviewText(null)", context), "");
  assert.equal(vm.runInContext('minorUnitEurPreviewText("abc")', context), "");
  const markup = vm.runInContext('minorUnitEurPreview("-2500")', context);
  assert.ok(markup.includes("data-eur-preview"));
  assert.ok(markup.includes("review.irpfPreview:€-25.00"));
}

console.log(JSON.stringify({ok: true}));
