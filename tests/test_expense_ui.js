const assert = require("node:assert/strict");
require("../src/autonomo_taxes/web_ui/status-help.js");
require("../src/autonomo_taxes/web_ui/app.js");
const hooks = globalThis.__AUTONOMO_WEB_UI_TEST_HOOKS__;

function scope(amount, count = 1) {
  return {amount_eur: amount, count, missing_amount_count: amount === null ? count : 0};
}
const summary = {
  purchase: {posted: scope("180.00"), approved: scope("60.00"), future_approved: scope("60.00"), reviewed_total: scope("240.00", 2)},
  amortization: {posted: scope("0.00", 0), approved: scope("60.00"), future_approved: scope("60.00"), reviewed_total: scope("60.00")},
};
const depreciation = {
  transaction_id: "depreciation", entry_type: "expense", expense_kind: "amortization", period_key: "2026-Q3",
  transaction_date: "2026-09-30", document_issued_on: "2025-02-10", document_amount_eur: "1200.00",
  amount_eur: "1200.00", deductible_irpf_eur: "60.00", deductible_vat_eur: "0.00",
  lifecycle_status: "approved", is_future_dated: true, asset_match_count: 1,
  asset_id: "synthetic-asset", asset_description: "Example workstation", asset_match_method: "inferred",
  counterparty_name: "Example supplier <script>alert(1)</script>", document_number: "TEST-INVOICE-001", document_id: "synthetic-document",
};
const purchase = {...depreciation, transaction_id: "purchase", expense_kind: "purchase", amount_eur: "60.00", asset_id: null,
  asset_match_count: 0, document_amount_eur: "60.00", deductible_irpf_eur: "50.00", deductible_vat_eur: "10.00"};
function payload(rows = [depreciation, purchase], overrides = {}) {
  return {as_of: "2026-08-20", rows, summary, matching_counts: {purchase: 1, amortization: 1},
    period_counts: {purchase: 2, amortization: 1}, has_more: false, next_offset: rows.length, ...overrides};
}

const ru = hooks.renderExpensePreview(payload(), "ru");
assert.equal((ru.match(/<section/g) || []).length, 2);
assert(ru.indexOf("Покупки, услуги") < ru.indexOf("Амортизация техники"));
assert(ru.includes("Это не новая покупка и не платёж"));
assert(ru.includes("data-status-help="));
for (const term of ["posting", "IRPF", "IVA"]) assert(ru.includes(`data-help-term="${term}"`));
assert(ru.includes("III квартал 2026"));
assert(ru.includes("Дата учёта ещё не наступила"));
assert(ru.includes("Документ от"));
assert(ru.includes("Сопоставлено по контрагенту"));
assert(!ru.includes("<script>"));
assert(ru.includes("&lt;script&gt;"));
const amortBlock = ru.slice(ru.indexOf('<section class="panel expense-section" aria-labelledby="expenses-amortization"'));
assert(amortBlock.includes("Амортизация за квартал"));
assert(!amortBlock.includes('data-label="IVA"'));
assert(!amortBlock.includes("Вычет IRPF"));
assert(amortBlock.includes("60,00"));
assert(/<small>Сумма документа:/.test(amortBlock));
const en = hooks.renderExpensePreview(payload(), "en");
assert(en.includes("Q3 2026"));
assert(en.includes("This is not a new purchase or a payment."));
assert(!en.includes("Амортизация"));
assert.deepEqual(hooks.expenseLocaleKeys().ru.sort(), hooks.expenseLocaleKeys().en.sort());

const zero = hooks.renderExpensePreview(payload([{...depreciation, deductible_irpf_eur: 0}]), "en");
assert(zero.includes("€0.00") || zero.includes("€0.00".replace("€", "")));
const negative = hooks.renderExpensePreview(payload([{...depreciation, deductible_irpf_eur: "-3.00"}]), "en");
assert(negative.includes("3.00"));
assert(negative.includes("-"));
const missing = hooks.renderExpensePreview(payload([{...depreciation, deductible_irpf_eur: null, asset_match_count: 2, asset_id: null}]), "en");
assert(missing.includes("Asset not matched"));
assert(missing.includes("Amount information is missing"));
assert(!missing.includes("Example workstation"));
assert(!missing.includes('href="/assets"'));
const nonzeroVat = hooks.renderExpensePreview(payload([{...depreciation, deductible_vat_eur: "4.00"}]), "en");
assert(nonzeroVat.includes("IVA:"));
assert(hooks.renderExpensePreview(payload([], {matching_counts: {purchase: 0, amortization: 0}}), "en").includes("No matching entries"));
assert(hooks.renderExpensePreview(payload([]), "en").includes("subsequent pages"));
assert(hooks.renderExpensePreview(payload([], {matching_counts: {purchase: 0, amortization: 0}, period_counts: {purchase: 0, amortization: 0}}), "en").includes("No entries in this quarter"));
const recent = hooks.renderRecentPreview([depreciation], "en");
assert(recent.includes("Quarterly depreciation"));
assert(recent.includes("In depreciation amount"));
assert(!recent.includes('<td class="amount">€1,200.00'));
const metric = hooks.renderExpenseMetric("amortization", summary, "en");
assert(metric.includes("Reviewed, not posted"));
assert(metric.includes("future-dated"));
assert(!metric.includes("Ready to post"));
assert(!metric.includes("1,200"));

const returnTo = "/expenses?period=2026-Q3&q=workstation";
const postedRows = [depreciation, purchase].map((row, index) => ({...row,
  lifecycle_status: "posted", transaction_id: `11111111-1111-4111-8111-11111111111${index}`,
}));
const postedHtml = hooks.renderExpensePreview(payload(postedRows), "en", returnTo);
assert.equal((postedHtml.match(/class="expense-document-link"/g) || []).length, 2);
for (const row of postedRows) assert(postedHtml.includes(`/expenses/${row.transaction_id}`));
assert(postedHtml.includes(encodeURIComponent(returnTo)));
assert(!postedHtml.includes("/review/"));

for (const attributes of [{id: "dashboard-ready-banner"}, {"data-copy-transaction-id": "copy-source"}, {href: "/api/document/example/content"}, {"data-status-help": "help-key"}, {"data-help-term": "posting"}]) {
  let restored = false;
  const node = {
    id: attributes.id,
    getAttribute: key => attributes[key] || null,
    closest: selector => selector === "tr" ? {dataset: {transactionId: "same-row"}} : null,
    focus: options => { assert.equal(options.preventScroll, true); restored = true; },
  };
  globalThis.document = {activeElement: node};
  hooks.replaceExpenseResults({contains: () => true, querySelectorAll: () => [node], innerHTML: "old"}, "new");
  assert.equal(restored, true);
}
delete globalThis.document;

assert(AccountingHelp.cell({domain:"issue", subject_id:"issue-two", state:"unknown"}).includes('data-status-subject="issue:issue-two"'));
let focusedIssue = null;
const issueButton = key => ({
  getAttribute: name => ({"data-status-help":"registration", "data-status-subject":`issue:${key}`}[name] || null),
  closest: () => null,
  focus: () => { focusedIssue = key; },
});
globalThis.document = {activeElement: issueButton("two")};
hooks.replaceExpenseResults({contains:()=>true, querySelectorAll:()=>[issueButton("one"),issueButton("two")], innerHTML:"old"}, "new");
assert.equal(focusedIssue, "two");
delete globalThis.document;

let focusedSection = null;
const termButton = section => ({
  getAttribute: key => key === "data-help-term" ? "IRPF" : null,
  closest: selector => selector === ".expense-section" ? {getAttribute: () => section} : null,
  focus: () => { focusedSection = section; },
});
globalThis.document = {activeElement: termButton("expenses-amortization")};
hooks.replaceExpenseResults({contains:()=>true,querySelectorAll:()=>[termButton("expenses-purchase"),termButton("expenses-amortization")],innerHTML:"old"}, "new");
assert.equal(focusedSection, "expenses-amortization");
delete globalThis.document;

const focusedSummary = {};
globalThis.document = {activeElement: focusedSummary};
const oldSlot = {id: "chart-business-result", querySelector: selector => selector === "details" ? {open: true} : focusedSummary};
const chartState = hooks.captureDashboardChartState({querySelector: () => oldSlot, querySelectorAll: () => [oldSlot]});
const newDetails = {open: false};
let chartFocusRestored = false;
const newSummary = {focus: options => { assert.equal(options.preventScroll, true); chartFocusRestored = true; }};
const newSlot = {id: oldSlot.id, querySelector: selector => selector === "details" ? newDetails : newSummary};
hooks.restoreDashboardChartState({querySelectorAll: () => [newSlot]}, chartState);
assert.equal(newDetails.open, true);
assert.equal(chartFocusRestored, true);
assert.equal(hooks.captureDashboardChartState({querySelector: () => null}), null);
delete globalThis.document;

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}

async function testPaging() {
  const calls = [];
  const pager = hooks.createExpensePager(options => {
    const pending = deferred(); calls.push({...options, ...pending}); return pending.promise;
  });
  const obsolete = pager.load("old");
  const newest = pager.load("new");
  calls[1].resolve(payload([purchase]));
  assert.equal((await newest).rows[0].transaction_id, "purchase");
  calls[0].resolve(payload([depreciation]));
  assert.equal(await obsolete, null);

  const more = pager.load("new", true);
  assert.equal(calls[2].offset, 1);
  calls[2].resolve(payload([purchase, depreciation], {next_offset: 3}));
  const combined = await more;
  assert.equal(combined.rows.length, 2);
  assert.equal(combined.next_offset, 3);

  const staleError = pager.load("bad");
  pager.invalidate();
  calls[3].reject(new Error("old network error"));
  assert.equal(await staleError, null);
  const failed = pager.load("retry");
  calls[4].reject(new Error("network failure"));
  await assert.rejects(failed, /network failure/);
  const retried = pager.load("retry");
  assert.equal(calls[5].offset, 0);
  calls[5].resolve(payload([depreciation]));
  assert.equal((await retried).rows.length, 1);

  const dates = [];
  const datePager = hooks.createExpensePager(async options => {
    dates.push(options.offset);
    return payload([depreciation], {as_of: dates.length === 1 ? "2026-09-29" : "2026-09-30"});
  });
  await datePager.load("");
  const fresh = await datePager.load("", true);
  assert.deepEqual(dates, [0, 1, 0]);
  assert.equal(fresh.as_of, "2026-09-30");
  assert.equal(fresh.rows.length, 1);

  const refreshOffsets = [];
  let revision = "first";
  const windowPager = hooks.createExpensePager(async ({offset}) => {
    refreshOffsets.push(offset);
    return payload([0, 1].map(i => ({...purchase, transaction_id: String(i + offset)})),
      {has_more: offset < 4, next_offset: offset + 2, view_revision: revision});
  });
  await windowPager.load("");
  assert.equal((await windowPager.load("", true)).rows.length, 4);
  assert.equal((await windowPager.refresh("")).rows.length, 4);
  assert.deepEqual(refreshOffsets, [0, 2, 0, 2]);
  revision = "changed-same-day";
  const updated = await windowPager.load("", true);
  assert.equal(updated.rows.length, 6);
  assert.equal(updated.view_revision, revision);
  assert.deepEqual(refreshOffsets.slice(-4), [4, 0, 2, 4]);

  const errors = [];
  assert.equal(await hooks.refreshDashboardExpenses(async () => { throw new Error("read failed"); }, error => errors.push(error.message)), false);
  assert.deepEqual(errors, ["read failed"]);
  assert.equal(await hooks.refreshDashboardExpenses(async () => {}, error => errors.push(error.message)), true);
  const olderRefresh = deferred();
  const newerRefresh = deferred();
  const oldRead = hooks.refreshDashboardExpenses(() => olderRefresh.promise, error => errors.push(error.message));
  const newRead = hooks.refreshDashboardExpenses(() => newerRefresh.promise, error => errors.push(error.message));
  newerRefresh.resolve();
  await newRead;
  olderRefresh.reject(new Error("obsolete failure"));
  await oldRead;
  assert.deepEqual(errors, ["read failed"]);
}

testPaging().then(() => console.log("Expense UI behavior checks passed")).catch(error => { console.error(error); process.exitCode = 1; });
