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

// Intake drafts round-trip through versioned storage, a pristine form clears
// the stored draft, and foreign schema versions are discarded.
{
  const storage = new Map();
  const intakeContext = vm.createContext({
    JSON,
    Object,
    localStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
  });
  const keyMatch = appSource.match(/const INTAKE_DRAFT_STORAGE_KEY = [^\n]+/);
  const fieldsMatch = appSource.match(/const INTAKE_DRAFT_FIELDS = [^\n]+/);
  if (!keyMatch || !fieldsMatch) throw new Error("Could not find intake draft constants in app.js");
  vm.runInContext(keyMatch[0], intakeContext);
  vm.runInContext(fieldsMatch[0], intakeContext);
  for (const name of [
    "readIntakeDraftValues",
    "intakeDraftIsEmpty",
    "persistIntakeDraft",
    "loadIntakeDraft",
    "clearIntakeDraft",
    "applyIntakeDraft",
  ]) {
    vm.runInContext(extractFunction(appSource, name), intakeContext);
  }
  const makeForm = (values) => {
    const elements = {};
    for (const name of ["issued_on", "counterparty_name", "document_number", "currency", "gross", "taxable_base", "vat", "drive_url"]) {
      elements[name] = {value: values[name] ?? ""};
    }
    return {elements};
  };
  intakeContext.intakeForm = makeForm({issued_on: "2026-07-01", counterparty_name: "ACME Test", currency: "EUR", gross: "121.00"});
  vm.runInContext("persistIntakeDraft()", intakeContext);
  const stored = JSON.parse(storage.get("autonomo.intake-draft"));
  assert.equal(stored.schema, 1);
  assert.equal(stored.values.counterparty_name, "ACME Test");
  const target = makeForm({});
  intakeContext.__target = target;
  assert.equal(vm.runInContext("applyIntakeDraft(__target.elements)", intakeContext), true);
  assert.equal(target.elements.gross.value, "121.00");
  assert.equal(target.elements.issued_on.value, "2026-07-01");
  intakeContext.intakeForm = makeForm({currency: "EUR"});
  vm.runInContext("persistIntakeDraft()", intakeContext);
  assert.equal(storage.has("autonomo.intake-draft"), false);
  storage.set("autonomo.intake-draft", JSON.stringify({schema: 2, values: {gross: "5"}}));
  assert.equal(vm.runInContext("loadIntakeDraft()", intakeContext), null);
  const untouched = makeForm({});
  intakeContext.__target = untouched;
  assert.equal(vm.runInContext("applyIntakeDraft(__target.elements)", intakeContext), false);
  assert.equal(untouched.elements.gross.value, "");
}

console.log(JSON.stringify({ok: true}));
