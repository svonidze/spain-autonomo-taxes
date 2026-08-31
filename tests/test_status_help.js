const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui");
const context = vm.createContext({
  console,
  URLSearchParams,
  Intl,
  Date,
  setTimeout,
  clearTimeout,
});
vm.runInContext(
  fs.readFileSync(path.join(root, "status-help.js"), "utf8"),
  context,
);
const source = fs.readFileSync(path.join(root, "app.js"), "utf8");
vm.runInContext(source, context);
const help = context.AccountingHelp;
const app = context.__AUTONOMO_WEB_UI_TEST_HOOKS__;
for (const name of [
  "t",
  "intlLocale",
  "countNoun",
  "obligationMap",
  "formCardData",
  "formEmptyState",
]) {
  assert.equal(
    source.split(`function ${name}(`).length - 1,
    1,
    `${name} must have one effective definition`,
  );
}
assert.equal(help.money(0), "0,00 €");
assert.equal(help.money(null), "Нет данных");
assert.equal(help.money(""), "Нет данных");
assert.match(
  help.label({ domain: "counterparty", state: "unknown" }),
  /ROI не проверена/,
);
assert.equal(
  help.tone({ domain: "counterparty", state: "unknown" }),
  "neutral",
);
assert.equal(
  help.label({ domain: "obligation", state: "not_due" }),
  "Подача не требуется",
);
const asset = {
  domain: "asset",
  state: "needs_review",
  facts: { advisor_decision: null },
  reasons: [
    { code: "advance_documents_overlap", blocking: true },
    { code: "iva_prior_deduction_unconfirmed", blocking: true },
  ],
};
assert.match(help.label(asset), /проверки авансов и IVA/);
assert.match(help.content(asset), /действующий комплект/);
const hostile = help.content({
  domain: "issue",
  state: "blocked",
  reasons: [{ code: "unknown_fixture", message: "<script>alert(1)</script>" }],
});
assert(!hostile.includes("<script>"));
assert(hostile.includes("&lt;script&gt;"));
assert(hostile.includes("Для этой причины пока нет подробной инструкции"));
assert.equal(
  help.label({ domain: "asset", state: "recorded_information" }),
  "Есть исходные записи",
);
const blocked = {
  supported: true,
  review_allowed: false,
  ui_context: { state: "blocked" },
  posting_context: {
    preview_bucket: "blocked",
    posting_deferred_until: "2099-10-01",
  },
};
assert.equal(app.evaluateWorkItemPosting(blocked).category, "blocked");
assert.equal(app.evaluateWorkItemPosting(blocked).canApply, false);
assert.equal(
  app.evaluateWorkItemPosting({ supported: true }).category,
  "blocked",
  "missing server context fails closed",
);
const later = {
  ...blocked,
  ui_context: { state: "deferred" },
  posting_context: {
    preview_bucket: "deferred",
    posting_deferred_until: "2099-10-01",
  },
};
assert.equal(app.evaluateWorkItemPosting(later).category, "later");
const normalized = app.normalizePostingItem({
  transaction_id: "11111111-1111-4111-8111-111111111111",
  preview_bucket: "blocked",
  posting_deferred_until: "2099-10-01",
  blockers: [
    {
      code: "blocking_issue",
      subject_table: "documents",
      subject_id: "doc",
      details: { issue_code: "fixture_issue" },
    },
  ],
});
assert.equal(normalized.previewBucket, "blocked");
assert.equal(
  normalized.structuredReasons[0].details.issue_code,
  "fixture_issue",
);
assert.equal(normalized.structuredReasons[0].subject_id, "doc");
assert.equal(
  app.routePathFor(
    "review",
    "transaction:11111111-1111-4111-8111-111111111111",
  ),
  "/review/11111111-1111-4111-8111-111111111111",
);
assert.equal(
  app.summarizeReviewRows([
    { ui_context: { state: "ready" } },
    { ui_context: { state: "blocked" } },
    { ui_context: { state: "needs_review" } },
    { ui_context: { state: "deferred" } },
  ]).ready,
  1,
);
help.setLocale("en");
assert.equal(
  help.label({ domain: "counterparty", state: "unknown" }),
  "ROI registration unverified",
);
assert.equal(help.money(null), "No data");
console.log("Status explanations: effective-script behavior verified");

// Exercise the actual delegated copy handler without touching a real clipboard.
(async () => {
  const handlers = new Map();
  let copied = null;
  const feedback = { textContent: "" };
  const question = { textContent: "Synthetic supplier question" };
  const article = {
    querySelector: (selector) =>
      selector === ".copy-question" ? question : feedback,
  };
  const copyButton = { closest: () => article };
  const domContext = vm.createContext({
    Intl,
    Date,
    console,
    navigator: {
      clipboard: {
        writeText: async (text) => {
          copied = text;
        },
      },
    },
    document: {
      addEventListener: (type, handler) => handlers.set(type, handler),
      getElementById: () => null,
    },
  });
  vm.runInContext(
    fs.readFileSync(path.join(root, "status-help.js"), "utf8"),
    domContext,
  );
  const click = {
    target: {
      closest: (selector) =>
        selector === "[data-copy-question]" ? copyButton : null,
    },
  };
  await handlers.get("click")(click);
  assert.equal(copied, "Synthetic supplier question");
  assert.match(feedback.textContent, /никуда не отправлен/);
  domContext.navigator.clipboard.writeText = async () => {
    throw new Error("denied");
  };
  await handlers.get("click")(click);
  assert.match(feedback.textContent, /Не удалось скопировать/);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
help.setLocale("ru");
assert.equal(
  help.summary({
    domain: "issue",
    state: "warning",
    reasons: [{ code: "counterparty_tax_profile_review", blocking: false }],
    posting: { blockers: [{ code: "lifecycle_not_approved" }] },
  }),
  "Нужны реквизиты контрагента",
);
assert.match(
  help.summary({
    domain: "transaction",
    state: "blocked",
    posting: { blockers: [{ code: "cleanup_precondition_failed" }] },
  }),
  /Исходный файл/,
);

// The merged assets view must retain explanations AND mount the analytics chart.
(async () => {
  const renderSource = source.slice(
    source.indexOf('async function renderAssets('),
    source.indexOf('async function renderTaxes('),
  );
  const rendered = {innerHTML: ''};
  const requests = [];
  const mounts = [];
  const builder = () => ({});
  const renderContext = vm.createContext({
    app: rendered,
    state: {period: '2032-Q2'},
    currentRenderGeneration: 7,
    fetchJSON: async url => {
      requests.push(url);
      return [{asset_code: 'SYN-MERGE-ASSET', cost_minor: 121000,
        amortizable_base_minor: 100000, placed_in_service_on: '2032-04-01',
        ui_context: {domain: 'asset', state: 'needs_review', reasons: [],
          amortization: {count: 1, rows: [{include_in_books: false}], excluded_minor: 10000}}}];
    },
    AccountingHelp: help,
    escapeHtml: value => String(value ?? ''),
    formatDate: value => value,
    t: key => key,
    emptyRow: () => '',
    buildAmortizationSpec: builder,
    mountViewAnalyticsChart: (id, spec) => mounts.push({id, spec}),
  });
  vm.runInContext(renderSource, renderContext);
  await renderContext.renderAssets(7);
  assert.equal(requests[0], '/api/assets?period=2032-Q2');
  assert.match(rendered.innerHTML, /status-details-button/);
  assert.match(rendered.innerHTML, /id="chart-amortization"/);
  assert.equal(mounts.length, 1);
  assert.equal(mounts[0].id, 'chart-amortization');
  assert.equal(mounts[0].spec, builder);
  const previous = rendered.innerHTML;
  await renderContext.renderAssets(6);
  assert.equal(rendered.innerHTML, previous);
  assert.equal(mounts.length, 1, 'A stale render must not mount a chart');
})().catch(error => {console.error(error); process.exitCode = 1;});
