const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const asyncMarker = `async function ${name}(`;
  const start = source.indexOf(asyncMarker) !== -1 ? source.indexOf(asyncMarker) : source.indexOf(marker);
  if (start === -1) throw new Error(`Could not find ${name} in app.js`);
  const signatureEnd = source.indexOf(")", start);
  const braceStart = source.indexOf("{", signatureEnd);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Could not parse ${name} in app.js`);
}

function loadFunctions(names, additions = {}) {
  const source = fs.readFileSync(appPath, "utf8");
  const context = {
    URL,
    URLSearchParams,
    String,
    Number,
    Array,
    Object,
    Set,
    JSON,
    Intl,
    Date,
    encodeURIComponent,
    ...additions,
  };
  names.forEach((name) => {
    vm.runInNewContext(`${extractFunction(source, name)}; this.${name} = ${name};`, context);
  });
  return context;
}

function loadAppWithRoutes(pathname = "/expenses", search = "") {
  class Element {}
  const location = {origin: "https://taxes.test", pathname, search};
  const historyCalls = [];
  const window = {
    location,
    history: {
      pushState(_state, _title, url) {
        const next = new URL(url, location.origin);
        location.pathname = next.pathname;
        location.search = next.search;
        historyCalls.push({kind: "push", url: `${next.pathname}${next.search}`});
      },
      replaceState(_state, _title, url) {
        const next = new URL(url, location.origin);
        location.pathname = next.pathname;
        location.search = next.search;
        historyCalls.push({kind: "replace", url: `${next.pathname}${next.search}`});
      },
    },
  };
  const context = vm.createContext({
    URL, URLSearchParams, URLSearchParams, Map, Set, Intl, JSON, String, Number, Array, Object,
    Boolean, Promise, Date, RegExp, Error, console, encodeURIComponent, decodeURIComponent,
    setTimeout, clearTimeout, Element, window,
  });
  vm.runInContext(fs.readFileSync(appPath, "utf8"), context);
  vm.runInContext(`
    state.bootstrap = {default_period: "2026-Q3", periods: [{period_key: "2026-Q2", status: "closed"}, {period_key: "2026-Q3", status: "open"}], intake_enabled: false};
    state.period = "2026-Q3";
    applyViewState = () => {};
    renderCurrentView = () => { globalThis.__routeRenders = (globalThis.__routeRenders || 0) + 1; return Promise.resolve(); };
  `, context);
  return {context, location, historyCalls, Element};
}

const appPath = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js");
const EXPENSE_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_EXPENSE_ID = "22222222-2222-4222-8222-222222222222";

async function main() {
// API failures retain both the expense HTTP status and status-help error code.
{
  let status = 404;
  let code = "transaction_not_found";
  const errors = loadFunctions(["fetchJSON", "parseJSONText"], {
    window: {location: {origin: "https://taxes.test"}},
    fetch: async () => ({ok: false, status, headers: {get: () => "application/json"},
      text: async () => JSON.stringify({error: "Synthetic error", code})}),
  });
  await assert.rejects(errors.fetchJSON(`/api/transactions/${EXPENSE_ID}`),
    (error) => error.status === 404 && error.code === "transaction_not_found");
  status = 403;
  code = "session_forbidden";
  await assert.rejects(errors.fetchJSON("/api/bootstrap"),
    (error) => error.status === 403 && error.code === "session_forbidden");
}
// The route carries raw transaction UUIDs and preserves a list's period and search.
{
  const state = {period: "2026-Q3", bootstrap: {periods: [{period_key: "2026-Q2"}, {period_key: "2026-Q3"}]}};
  const routing = loadFunctions(
    ["parseRoute", "routePathFor", "buildRouteUrl", "routePeriodFromQuery", "safeReturnUrl"],
    {
      state,
      ROUTE_VIEWS: {"/dashboard": "dashboard", "/expenses": "expenses", "/review": "review"},
      REVIEW_DETAIL_RE: /^\/review\/([0-9a-fA-F-]{32,36})$/,
      EXPENSE_DETAIL_RE: /^\/expenses\/([0-9a-fA-F-]{32,36})$/,
      PERIOD_ROUTE_PATHS: new Set(["/dashboard", "/expenses", "/review"]),
      window: {location: {origin: "https://taxes.test"}},
    },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(routing.parseRoute(`/expenses/${EXPENSE_ID}`))),
    {view: "expense-detail", reviewId: null, transactionId: EXPENSE_ID},
  );
  assert.equal(
    routing.buildRouteUrl("expense-detail", {
      transactionId: EXPENSE_ID,
      period: "2026-Q2",
      returnTo: "/expenses?period=2026-Q2&q=SYN",
    }),
    `/expenses/${EXPENSE_ID}?period=2026-Q2&returnTo=%2Fexpenses%3Fperiod%3D2026-Q2%26q%3DSYN`,
  );
  assert.equal(
    routing.buildRouteUrl("review", {reviewId: `transaction:${EXPENSE_ID}`, period: "2026-Q2"}),
    `/review/${EXPENSE_ID}?period=2026-Q2`,
  );
  assert.equal(routing.safeReturnUrl("/expenses?period=2026-Q2&q=SYN", "2026-Q2"), "/expenses?period=2026-Q2&q=SYN");
  assert.equal(routing.safeReturnUrl("https://attacker.test/expenses?period=2026-Q2", "2026-Q2"), "/expenses?period=2026-Q2");
  assert.equal(routing.safeReturnUrl("//attacker.test/expenses?period=2026-Q2", "2026-Q2"), "/expenses?period=2026-Q2");
}

// A posted expense gets its own read-only link; income retains its normal document text.
{
  const actions = loadFunctions(["transactionDocumentLink"], {
    t: (key) => ({"expense.open": "Open expense"}[key] || key),
    escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
    encodeURIComponent,
    buildRouteUrl: (view, options) => `${view}:${options.transactionId}:${options.returnTo}`,
  });
  const expenseMarkup = actions.transactionDocumentLink(
    {transaction_id: EXPENSE_ID, entry_type: "expense", lifecycle_status: "posted", document_id: "synthetic-document"},
    "/expenses?period=2026-Q2&q=SYN",
  );
  assert.match(expenseMarkup, /data-spa/);
  assert.match(expenseMarkup, new RegExp(`expense-detail:${EXPENSE_ID}:/`));
  const incomeMarkup = actions.transactionDocumentLink(
    {transaction_id: OTHER_EXPENSE_ID, entry_type: "income", lifecycle_status: "posted", document_number: "income-doc"},
    "/income?period=2026-Q2",
  );
  assert.doesNotMatch(incomeMarkup, /Open expense/);
  assert.equal(incomeMarkup, "income-doc");
}

// Stored notes are complete escaped text, each treatment is retained, and the view has no decision controls.
{
  const markup = loadFunctions(["expenseDetailMarkup"], {
    state: {locale: "en", period: "2026-Q2", returnTo: "/expenses?period=2026-Q2&q=SYN"},
    t: (key) => key,
    escapeHtml: (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"),
    formatDate: (value) => value || "—",
    eur: (value) => value === null || value === undefined ? "—" : `€${value}`,
    intlLocale: () => "en-US",
    badge: (value) => `<span>${value}</span>`,
    statusLabel: (value) => value,
    documentTypeLabel: (value) => value,
    boolText: (value) => String(Boolean(value)),
    taxCodeLabel: (value) => value,
    buildRouteUrl: () => "/expenses?period=2026-Q2&q=SYN",
    safeReturnUrl: () => "/expenses?period=2026-Q2&q=SYN",
    expenseBackLink: () => '<a data-spa href="/expenses?period=2026-Q2&q=SYN">back</a>',
    encodeURIComponent,
  });
  const page = markup.expenseDetailMarkup({
    transaction: {transaction_id: EXPENSE_ID, entry_type: "expense", lifecycle_status: "posted", transaction_date: "2026-06-19", description: "Synthetic expense", amount_minor: 0, amount_eur_minor: 0, currency: "EUR"},
    period: {period_key: "2026-Q2", status: "closed"},
    document: {document_id: "synthetic-doc", document_number: "SYN-EXP-0042", document_type: "expense_invoice", issued_on: "2026-06-19"},
    counterparty: {display_name: "Synthetic supplier", country_code: "ES"},
    tax_treatments: [
      {treatment_type: "income_tax", jurisdiction: "ES", tax_code: "expense_general", deductible_irpf_minor: 0, deductible_vat_minor: 0, notes: "First note\n<script>alert(1)</script>"},
      {treatment_type: "vat", jurisdiction: "ES-IVA", tax_code: "domestic_input", deductible_irpf_minor: 0, deductible_vat_minor: 0, notes: "Second preserved note"},
    ],
  });
  assert.match(page, /SYN-EXP-0042/);
  assert.match(page, /First note\n&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(page, /Second preserved note/);
  assert.doesNotMatch(page, /<script>/);
  assert.doesNotMatch(page, /review-primary-button|review-reject-button|post-ready|<form\b/);
  const noNotes = markup.expenseDetailMarkup({
    transaction: {transaction_id: OTHER_EXPENSE_ID, entry_type: "expense", lifecycle_status: "included_in_snapshot"},
    period: {period_key: "2026-Q2", status: "closed"}, document: null, counterparty: null,
    tax_treatments: [{treatment_type: "vat", jurisdiction: "ES-IVA", notes: ""}, {treatment_type: "income_tax", jurisdiction: "ES", notes: null}],
  });
  assert.match(noNotes, /expense\.noNotes/);
}

// Detail requests are GET helpers, stale responses cannot replace a newer card, and refresh only rereads it.
{
  const calls = [];
  const detail = loadFunctions(["fetchTransactionDetail", "renderExpenseDetail", "refreshDashboard"], {
    state: {
      view: "expense-detail",
      period: "2026-Q3",
      expenseDetail: {transactionId: EXPENSE_ID, data: null},
      posting: {},
    },
    currentRenderGeneration: 7,
    fetchJSON: async (url, options) => {
      calls.push({url, options});
      return {period: {period_key: "2026-Q2"}, transaction: {transaction_id: EXPENSE_ID, entry_type: "expense"}};
    },
    detailRouteActive: (view, id, generation) => view === "expense-detail" && id === EXPENSE_ID && generation === 7,
    applyDetailPeriod: () => calls.push({kind: "period"}),
    safeReturnUrl: () => "/expenses?period=2026-Q2&q=SYN",
    expenseDetailMarkup: () => "detail markup",
    app: {innerHTML: "", querySelector: () => null},
    renderCurrentView: async () => calls.push({kind: "render"}),
    refreshButton: {disabled: false, textContent: "↻"},
    requestDashboardRefresh: async () => calls.push({kind: "write-refresh"}),
    showToast: () => {},
  });
  await detail.fetchTransactionDetail(EXPENSE_ID);
  assert.deepEqual(calls[0], {url: `/api/transactions/${EXPENSE_ID}`, options: undefined});
  await detail.renderExpenseDetail(7);
  assert.equal(detail.app.innerHTML, "detail markup");
  assert.equal(detail.state.expenseDetail.data.transaction.transaction_id, EXPENSE_ID);
  await detail.refreshDashboard();
  assert.equal(calls.some((call) => call.kind === "write-refresh"), false);
  assert.equal(calls.filter((call) => call.kind === "render").length, 1);

  let resolveStaleDetail;
  const stale = loadFunctions(["detailRouteActive", "renderExpenseDetail"], {
    state: {view: "expense-detail", expenseDetail: {transactionId: EXPENSE_ID, data: null}, returnTo: null},
    currentRenderGeneration: 9,
    fetchTransactionDetail: () => new Promise((resolve) => { resolveStaleDetail = resolve; }),
    applyDetailPeriod: () => { throw new Error("stale result changed the period"); },
    safeReturnUrl: () => { throw new Error("stale result rendered a card"); },
    expenseDetailMarkup: () => { throw new Error("stale result rendered markup"); },
    app: {innerHTML: "newer detail remains"},
  });
  const staleRender = stale.renderExpenseDetail(9);
  stale.state.expenseDetail.transactionId = OTHER_EXPENSE_ID;
  resolveStaleDetail({period: {period_key: "2026-Q2"}, transaction: {transaction_id: EXPENSE_ID, entry_type: "expense"}});
  await staleRender;
  assert.equal(stale.app.innerHTML, "newer detail remains");
  assert.equal(stale.state.expenseDetail.data, null);

  const rejected = loadFunctions(["renderCurrentView"], {
    state: {view: "expense-detail", period: "2026-Q2", expenseDetail: {transactionId: EXPENSE_ID}, review: {selectedReviewId: null}},
    currentRenderGeneration: 3,
    app: {innerHTML: "newer page remains", setAttribute: () => {}},
    AccountingHelp: {beforeRender: () => {}, setLocale: () => {}, labelTables: () => {
      throw new Error("stale render must not relabel the newer page");
    }},
    refreshCopyTargetState: () => {},
    incomeCopyRowsById: {clear: () => {}},
    viewChartRegistry: {clear: () => {}},
    closeChartDialog: () => {},
    closePostingConfirmDialog: () => {},
    closeCounterpartyMenu: () => {},
    escapeHtml: (value) => String(value),
    t: (key) => key,
    renderExpenseDetail: async () => { throw new Error("old request failed"); },
    errorState: (error) => `error:${error.message}`,
    uiLoadingSkeleton: () => "loading",
  });
  vm.runInNewContext("renderExpenseDetail = async () => { app.innerHTML = 'newer page remains'; currentRenderGeneration = 5; throw new Error('old request failed'); };", rejected);
  await rejected.renderCurrentView();
  assert.equal(rejected.app.innerHTML, "newer page remains");
}

// Leaving a review work item clears its editor state, and history restores the list query instead of a stale card.
{
  const app = loadAppWithRoutes(`/review/${EXPENSE_ID}`, "?period=2026-Q2");
  vm.runInContext(`
    state.review.selectedReviewId = "transaction:${EXPENSE_ID}";
    state.review.workItem = {packet: {review_id: "transaction:${EXPENSE_ID}"}};
    state.review.fxChoice = {mode: "ecb"};
    state.review.confirmError = {message: "old"};
    state.review.validationResult = {valid: true};
    state.review.error = "old";
    applyRouteFromLocation();
  `, app.context);
  let snapshot = JSON.parse(vm.runInContext("JSON.stringify(state)", app.context));
  assert.equal(snapshot.view, "review");
  assert.equal(snapshot.period, "2026-Q3");
  assert.equal(snapshot.review.selectedReviewId, `transaction:${EXPENSE_ID}`);

  app.location.pathname = "/review";
  app.location.search = "?period=2026-Q2";
  vm.runInContext("applyRouteFromLocation()", app.context);
  snapshot = JSON.parse(vm.runInContext("JSON.stringify(state)", app.context));
  assert.equal(snapshot.review.selectedReviewId, null);
  assert.equal(snapshot.review.workItem, null);
  assert.equal(snapshot.review.fxChoice, null);
  assert.equal(snapshot.review.confirmError, null);
  assert.equal(snapshot.review.validationResult, null);
  assert.equal(snapshot.review.error, "");

  app.location.pathname = "/expenses";
  app.location.search = "?period=2026-Q2&q=SYN";
  vm.runInContext("applyRouteFromLocation()", app.context);
  vm.runInContext(`navigateToUrl("/expenses/${EXPENSE_ID}?period=2026-Q2&returnTo=%2Fexpenses%3Fperiod%3D2026-Q2%26q%3DSYN")`, app.context);
  snapshot = JSON.parse(vm.runInContext("JSON.stringify(state)", app.context));
  assert.equal(snapshot.view, "expense-detail");
  assert.equal(snapshot.expenseDetail.transactionId, EXPENSE_ID);
  assert.equal(snapshot.returnTo, "/expenses?period=2026-Q2&q=SYN");

  // Simulate Back and Forward popstate applications without manufacturing new history entries.
  app.location.pathname = "/expenses";
  app.location.search = "?period=2026-Q2&q=SYN";
  vm.runInContext("applyRouteFromLocation()", app.context);
  snapshot = JSON.parse(vm.runInContext("JSON.stringify(state)", app.context));
  assert.equal(snapshot.view, "expenses");
  assert.equal(snapshot.expenseDetail.transactionId, null);
  assert.equal(snapshot.expensesQuery, "SYN");
  app.location.pathname = `/expenses/${EXPENSE_ID}`;
  app.location.search = "?period=2026-Q2&returnTo=%2Fexpenses%3Fperiod%3D2026-Q2%26q%3DSYN";
  vm.runInContext("applyRouteFromLocation()", app.context);
  assert.equal(app.historyCalls.filter((call) => call.kind === "push").length, 1);
}

// Legacy review URLs redirect posted expenses before the editable work item, while needs_review keeps that workspace in the operation quarter.
{
  const posted = loadAppWithRoutes(`/review/${EXPENSE_ID}`, "?period=2026-Q3");
  vm.runInContext(`
    state.view = "review";
    state.review.selectedReviewId = "transaction:${EXPENSE_ID}";
    fetchTransactionDetail = async () => ({transaction: {entry_type: "expense", lifecycle_status: "posted"}, period: {period_key: "2026-Q2"}});
    detailRouteActive = () => true;
    fetchJSON = async () => { throw new Error("posted expense must not load a work item"); };
    navigateToRoute = (view, options) => { globalThis.__legacyNavigation = {view, options}; };
  `, posted.context);
  await vm.runInContext("renderReview(0)", posted.context);
  assert.deepEqual(JSON.parse(vm.runInContext("JSON.stringify(__legacyNavigation)", posted.context)), {
    view: "expense-detail",
    options: {transactionId: EXPENSE_ID, period: "2026-Q2", returnTo: null, replace: true},
  });

  const needsReview = loadAppWithRoutes(`/review/${OTHER_EXPENSE_ID}`, "?period=2026-Q3");
  vm.runInContext(`
    state.view = "review";
    state.review.selectedReviewId = "transaction:${OTHER_EXPENSE_ID}";
    fetchTransactionDetail = async () => ({transaction: {entry_type: "expense", lifecycle_status: "needs_review"}, period: {period_key: "2026-Q2"}});
    detailRouteActive = () => true;
    applyDetailPeriod = (data) => { state.period = data.period.period_key; };
    fetchJSON = async () => ({packet: {review_id: "transaction:${OTHER_EXPENSE_ID}", state: {transaction: {transaction_id: "${OTHER_EXPENSE_ID}"}, period: {period_key: "2026-Q2"}}, decision: {}}});
    renderReviewWorkspace = () => { globalThis.__workspaceRendered = true; };
  `, needsReview.context);
  await vm.runInContext("renderReview(0)", needsReview.context);
  assert.equal(vm.runInContext("state.period", needsReview.context), "2026-Q2");
  assert.equal(vm.runInContext("__workspaceRendered", needsReview.context), true);
}

// Modified clicks keep browser behavior; a plain click preserves the complete query for SPA navigation.
{
  const app = loadAppWithRoutes("/expenses", "?period=2026-Q2&q=SYN");
  vm.runInContext("navigateToUrl = (url) => { globalThis.__clickedUrl = url; }", app.context);
  class Link extends app.Element {
    constructor() { super(); this.href = `/expenses/${EXPENSE_ID}?period=2026-Q2&returnTo=%2Fexpenses%3Fperiod%3D2026-Q2%26q%3DSYN`; }
    closest() { return this; }
    hasAttribute() { return false; }
    getAttribute() { return this.href; }
  }
  const link = new Link();
  const modified = {defaultPrevented: false, button: 0, ctrlKey: true, metaKey: false, shiftKey: false, altKey: false, target: link, preventDefault() { throw new Error("modified click intercepted"); }};
  vm.runInContext("handleSpaClick", app.context)(modified);
  assert.equal(vm.runInContext("typeof __clickedUrl", app.context), "undefined");
  let prevented = false;
  const plain = {...modified, ctrlKey: false, preventDefault() { prevented = true; }};
  vm.runInContext("handleSpaClick", app.context)(plain);
  assert.equal(prevented, true);
  assert.equal(vm.runInContext("__clickedUrl", app.context), link.href);
}

// A posting response that returns after navigation cannot refresh or rerender the new expense card; an active review still refreshes its own quarter.
{
  const ready = [{transactionId: EXPENSE_ID, expectedRowVersion: 1}];
  let resolvePosting;
  const inactiveCalls = [];
  let inactiveRenders = 0;
  let inactiveToasts = 0;
  const inactive = loadFunctions(["requestDashboardRefresh", "submitPostingReady"], {
    state: {
      view: "review", period: "2026-Q3",
      posting: {previewPeriod: "2026-Q3", preview: {ready}, pendingItems: [...ready], isSubmitting: false, staleRefresh: null},
    },
    currentRenderGeneration: 12,
    currentPostingPreview: () => inactive.state.posting.preview,
    postingActionDisabled: () => false,
    renderPostingConfirmDialog: () => {},
    closePostingConfirmDialog: () => {},
    buildPostReadyItems: () => [{transaction_id: EXPENSE_ID, expected_row_version: 1}],
    normalizePostingResult: () => ({status: "posted", summary: {postedCount: 1}}),
    postingResultToast: () => "posted",
    renderCurrentView: async () => { inactiveRenders += 1; },
    showToast: () => { inactiveToasts += 1; },
    postingConfirmDialog: {open: false},
    postingConfirmStatus: {textContent: ""},
    fetchJSON: (url, options) => {
      inactiveCalls.push({url, options});
      if (url === "/api/review/post-ready") return new Promise((resolve) => { resolvePosting = resolve; });
      throw new Error(`unexpected request ${url}`);
    },
    t: (key) => key,
  });
  const pendingSubmission = inactive.submitPostingReady();
  await Promise.resolve();
  inactive.state.view = "expense-detail";
  inactive.state.period = "2026-Q2";
  inactive.currentRenderGeneration += 1;
  resolvePosting({status: "posted"});
  await pendingSubmission;
  assert.equal(inactive.state.posting.lastResultPeriod, "2026-Q3");
  assert.deepEqual(JSON.parse(JSON.stringify(inactive.state.posting.staleRefresh)), {period: "2026-Q3", error: ""});
  assert.equal(inactiveCalls.filter((call) => call.url === "/api/dashboard/refresh").length, 0);
  assert.equal(inactiveRenders, 0);
  assert.equal(inactiveToasts, 0);
  assert.equal(inactive.state.posting.isSubmitting, false);

  const activeCalls = [];
  let activeRenders = 0;
  const active = loadFunctions(["requestDashboardRefresh", "submitPostingReady"], {
    state: {
      view: "review", period: "2026-Q3",
      posting: {previewPeriod: "2026-Q3", preview: {ready}, pendingItems: [...ready], isSubmitting: false, staleRefresh: {period: "2026-Q3", error: "old"}},
    },
    currentRenderGeneration: 20,
    currentPostingPreview: () => active.state.posting.preview,
    postingActionDisabled: () => false,
    renderPostingConfirmDialog: () => {},
    closePostingConfirmDialog: () => {},
    buildPostReadyItems: () => [{transaction_id: EXPENSE_ID, expected_row_version: 1}],
    normalizePostingResult: () => ({status: "posted", summary: {postedCount: 1}}),
    postingResultToast: () => "posted",
    renderCurrentView: async () => { activeRenders += 1; },
    showToast: () => {},
    postingConfirmDialog: {open: false},
    postingConfirmStatus: {textContent: ""},
    fetchJSON: async (url, options) => { activeCalls.push({url, options}); return {status: "ok"}; },
    t: (key) => key,
  });
  await active.submitPostingReady();
  assert.equal(activeCalls.filter((call) => call.url === "/api/review/post-ready").length, 1);
  const refresh = activeCalls.find((call) => call.url === "/api/dashboard/refresh");
  assert.ok(refresh);
  assert.equal(JSON.parse(refresh.options.body).period, "2026-Q3");
  assert.equal(active.state.posting.staleRefresh, null);
  assert.equal(activeRenders, 1);
}

// A deferred dashboard refresh cannot re-enable controls after navigation into an unresolved review detail, and that preflight itself stays GET-only.
{
  let resolveRefresh;
  let applyCalls = 0;
  let renders = 0;
  let toasts = 0;
  const refresh = loadFunctions(["refreshDashboard"], {
    state: {
      view: "dashboard", period: "2026-Q3", detailPeriodResolved: false,
      review: {selectedReviewId: null}, posting: {staleRefresh: null},
    },
    currentRenderGeneration: 30,
    refreshButton: {disabled: false, textContent: "↻", classList: {add: () => {}, remove: () => {}}},
    requestDashboardRefresh: () => new Promise((resolve) => { resolveRefresh = resolve; }),
    renderCurrentView: async () => { renders += 1; },
    showToast: () => { toasts += 1; },
    t: (key) => key,
    applyViewState: () => {
      applyCalls += 1;
      refresh.refreshButton.disabled = refresh.state.view === "review" && Boolean(refresh.state.review.selectedReviewId);
    },
  });
  const oldRefresh = refresh.refreshDashboard();
  assert.equal(refresh.refreshButton.disabled, true);
  refresh.state.view = "review";
  refresh.state.review.selectedReviewId = `transaction:${EXPENSE_ID}`;
  refresh.state.detailPeriodResolved = false;
  refresh.currentRenderGeneration += 1;
  resolveRefresh({status: "ok"});
  await oldRefresh;
  assert.equal(refresh.refreshButton.disabled, true);
  assert.equal(refresh.refreshButton.textContent, "↻");
  assert.equal(applyCalls, 1);
  assert.equal(renders, 0);
  assert.equal(toasts, 0);

  const preflightCalls = [];
  let preflightRenders = 0;
  const preflight = loadFunctions(["refreshDashboard"], {
    state: {
      view: "review", period: "2026-Q3", detailPeriodResolved: false,
      review: {selectedReviewId: `transaction:${EXPENSE_ID}`}, posting: {staleRefresh: null},
    },
    currentRenderGeneration: 31,
    refreshButton: {disabled: true, textContent: "↻"},
    requestDashboardRefresh: async (...args) => { preflightCalls.push(args); },
    renderCurrentView: async () => { preflightRenders += 1; },
    showToast: () => {}, t: (key) => key, applyViewState: () => {},
  });
  await preflight.refreshDashboard();
  assert.equal(preflightCalls.length, 0);
  assert.equal(preflightRenders, 1);
  assert.equal(preflight.refreshButton.disabled, true);
}

console.log("expense detail web ui checks OK");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
