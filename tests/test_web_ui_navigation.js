const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// This harness deliberately evaluates the production app verbatim.  It only
// replaces browser and HTTP surroundings, so clicks and popstate use the real
// registered handlers and routing/rendering functions.
const appSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js"),
  "utf8",
);
const statusHelpSource = fs.readFileSync(
  path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "status-help.js"),
  "utf8",
);
const Q2_ID = "11111111-1111-4111-8111-111111111111";
const Q3_ID = "22222222-2222-4222-8222-222222222222";
const REAL_DATE = Date;
class FrozenDate extends REAL_DATE {
  constructor(...args) {
    super(...(args.length ? args : ["2026-08-31T12:00:00.000Z"]));
  }
  static now() { return new REAL_DATE("2026-08-31T12:00:00.000Z").valueOf(); }
}

class FakeElement {
  constructor(attributes = {}) {
    this.attributes = {...attributes};
    this.dataset = {};
    for (const [key, value] of Object.entries(attributes)) {
      if (key.startsWith("data-")) {
        this.dataset[key.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = value;
      }
    }
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.files = [];
    this.style = {};
    this.open = false;
    this.listeners = new Map();
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  addEventListener(type, callback) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(callback);
    this.listeners.set(type, listeners);
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  hasAttribute(name) { return Object.hasOwn(this.attributes, name); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  querySelectorAll() { return []; }
  closest(selector) {
    if (selector === "a[data-spa]" && this.attributes.href && Object.hasOwn(this.attributes, "data-spa")) return this;
    if (selector === "[data-nav-view]" && this.dataset.navView) return this;
    if (selector === "[data-status-help]" && this.dataset.statusHelp) return this;
    if (selector === "#status-help-close" && this.attributes.id === "status-help-close") return this;
    return null;
  }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  select() {}
  contains() { return false; }
}

function packet(transactionId, period, snapshot = `snapshot-${transactionId}`) {
  return {
    packet: {
      review_id: `transaction:${transactionId}`,
      snapshot_hash: snapshot,
      state: {
        period: {period_key: period},
        transaction: {transaction_id: transactionId, transaction_date: period === "2026-Q2" ? "2026-05-15" : "2026-08-15", original_currency: "EUR"},
        issues: [],
      },
      ui_context: {domain: "transaction", state: "needs_review", reasons: []},
      decision: {},
    },
    requirements: [],
    fx_suggestion: null,
    ui_context: {domain: "transaction", state: "needs_review", reasons: []},
  };
}

function response(payload, ok = true) {
  return {
    ok,
    status: ok ? 200 : 500,
    headers: {get: () => "application/json"},
    text: async () => JSON.stringify(payload),
  };
}

function createHarness({initial = "/review?period=2026-Q2", deferred = false, deferDetailAt = null, deferDetailCalls = [], failDetailIds = [], workItemFactory = null, postedIds = []} = {}) {
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement());
    return elements.get(selector);
  };
  const documentListeners = new Map();
  const document = {
    documentElement: new FakeElement(),
    querySelector: element,
    querySelectorAll: () => [],
    getElementById: (id) => element(`#${id}`),
    body: {appendChild() {}},
    addEventListener(type, callback) {
      const listeners = documentListeners.get(type) || [];
      listeners.push(callback);
      documentListeners.set(type, listeners);
    },
  };
  const location = new URL(initial, "http://example.test");
  const historyEntries = [{url: location.pathname + location.search + location.hash, state: null}];
  let historyIndex = 0;
  const listeners = new Map();
  const intervals = [];
  const window = {
    location,
    scrollY: 0,
    scrollTo() {},
    setInterval(callback, delay) { intervals.push({callback, delay}); return intervals.length; },
    history: {
      get state() { return historyEntries[historyIndex].state; },
      get length() { return historyEntries.length; },
      pushState(nextState, _title, url) {
        const next = new URL(url, location.origin);
        historyEntries.splice(historyIndex + 1);
        historyEntries.push({url: next.pathname + next.search + next.hash, state: nextState});
        historyIndex += 1;
        location.href = next.href;
      },
      replaceState(nextState, _title, url) {
        const next = url === undefined
          ? new URL(location.href)
          : new URL(url, location.origin);
        historyEntries[historyIndex] = {url: next.pathname + next.search + next.hash, state: nextState};
        location.href = next.href;
      },
      back() { moveHistory(-1); },
      forward() { moveHistory(1); },
    },
    addEventListener(type, callback) {
      const callbacks = listeners.get(type) || [];
      callbacks.push(callback);
      listeners.set(type, callbacks);
    },
  };
  function dispatch(type, event) {
    for (const callback of listeners.get(type) || []) callback(event);
  }
  function moveHistory(delta) {
    const nextIndex = historyIndex + delta;
    if (nextIndex < 0 || nextIndex >= historyEntries.length) return;
    historyIndex = nextIndex;
    location.href = new URL(historyEntries[historyIndex].url, location.origin).href;
    dispatch("popstate", {});
  }
  const requests = [];
  const deferredRequests = [];
  let detailRequests = 0;
  const rowsFor = (period) => [{
    transaction_id: period === "2026-Q2" ? Q2_ID : Q3_ID,
    transaction_date: period === "2026-Q2" ? "2026-05-15" : "2026-08-15",
    lifecycle_status: "needs_review", counterparty_name: `Synthetic ${period}`, amount_eur: 100,
    open_issue_count: 0,
    ui_context: {domain: "transaction", state: "needs_review", reasons: []},
  }];
  const fetch = (url, options = {}) => {
    const text = String(url);
    if (options.method && options.method !== "GET") {
      throw new Error(`Unexpected non-GET fetch: ${options.method} ${text}`);
    }
    requests.push(text);
    const transactionMatch = /^\/api\/transactions\/([^/?]+)$/.exec(text);
    if (transactionMatch) {
      const id = decodeURIComponent(transactionMatch[1]);
      return Promise.resolve(response({
        transaction: {transaction_id: id, entry_type: "expense", lifecycle_status: postedIds.includes(id) ? "posted" : "needs_review"},
        period: {period_key: id === Q2_ID ? "2026-Q2" : "2026-Q3"},
        document: null, counterparty: null,
        tax_treatments: [{treatment_type: "invoice_review", jurisdiction: "ES", notes: "Synthetic saved note"}],
      }));
    }
    const detailMatch = /review_id=transaction%3A([^&]+)/.exec(text);
    if (detailMatch) {
      detailRequests += 1;
      const transactionId = decodeURIComponent(detailMatch[1]);
      const payload = workItemFactory
        ? workItemFactory(transactionId, detailRequests)
        : packet(transactionId, transactionId === Q2_ID ? "2026-Q2" : "2026-Q3");
      if (failDetailIds.includes(transactionId)) return Promise.resolve(response({error: "synthetic unknown review"}, false));
      if (deferred || detailRequests === deferDetailAt || deferDetailCalls.includes(detailRequests)) {
        return new Promise((resolve, reject) => deferredRequests.push({resolve, reject, payload}));
      }
      return Promise.resolve(response(payload));
    }
    if (text === "/api/bootstrap") return Promise.resolve(response({
      profile_name: "Synthetic profile", default_period: "2026-Q3", intake_enabled: false,
      periods: [{period_key: "2026-Q2", status: "closed"}, {period_key: "2026-Q3", status: "open"}],
    }));
    const period = new URL(text, location.origin).searchParams.get("period");
    if (text.startsWith("/api/expenses?")) return Promise.resolve(response({
      period, as_of: "2026-08-31", rows: [], matching_counts: {purchase: 0, amortization: 0},
      period_counts: {purchase: 0, amortization: 0}, has_more: false, next_offset: 0,
      summary: Object.fromEntries(["purchase", "amortization"].map(kind => [kind, {
        reviewed_total: {amount_eur: "0.00", count: 0, missing_amount_count: 0},
      }])),
    }));
    if (text.startsWith("/api/transactions?")) return Promise.resolve(response(rowsFor(period)));
    if (text.startsWith("/api/issues?")) return Promise.resolve(response([]));
    if (text.startsWith("/api/documents?")) return Promise.resolve(response([]));
    if (text.startsWith("/api/review/posting-preview?")) return Promise.resolve(response({period, summary: {}, ready: [], deferred: [], blocked: []}));
    if (text.startsWith("/api/analytics?")) return Promise.resolve(response({datasets: {review_aging: {counts: {}}, expense_structure: {buckets: []}}}));
    throw new Error(`Unexpected fetch: ${text}`);
  };
  const storage = new Map();
  const context = {
    console, URL, URLSearchParams, Element: FakeElement, document, window, fetch,
    localStorage: {getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, String(value)), removeItem: (key) => storage.delete(key)},
    setTimeout, clearTimeout, Map, Set, Date: FrozenDate, JSON, Math, Number, String, Array, Boolean, Object, RegExp,
    Intl, Promise, FormData: class { constructor() {} entries() { return []; } set() {} },
    AutonomoCharts: {renderHorizontalBars() {}, renderCartesian() {}, renderBullet() {}},
  };
  context.globalThis = context;
  vm.runInNewContext(statusHelpSource, context, {filename: "status-help.js"});
  vm.runInNewContext(appSource, context, {filename: "app.js"});
  const flush = async () => { for (let index = 0; index < 8; index += 1) await new Promise((resolve) => setImmediate(resolve)); };
  const click = async (href, eventProps = {}) => {
    const link = new FakeElement({href, "data-spa": ""});
    for (const callback of documentListeners.get("click") || []) {
      callback({target: link, button: 0, preventDefault() {}, ...eventProps});
    }
    await flush();
  };
  const clickNav = async (view) => {
    const button = new FakeElement({"data-nav-view": view});
    for (const callback of element("#app").listeners.get("click") || []) callback({target: button, button: 0, preventDefault() {}});
    await flush();
  };
  const clickStatusHelp = async (id) => {
    const button = new FakeElement({"data-status-help": id});
    for (const callback of documentListeners.get("click") || []) {
      await callback({target: button, button: 0, preventDefault() {}});
    }
    await flush();
  };
  const applyExternalLocation = async (url) => {
    window.history.pushState(null, "", url);
    dispatch("popstate", {});
    await flush();
  };
  const inspect = () => vm.runInContext("({period: state.period, view: state.view, selectedReviewId: state.review.selectedReviewId, workItem: state.review.workItem, html: app.innerHTML, disabled: periodSelect.disabled})", context);
  return {context, window, intervals, requests, deferredRequests, flush, click, clickNav, clickStatusHelp, applyExternalLocation, inspect, app: element("#app"), periodSelect: element("#period-select"), storage, element};
}

async function run(name, test) {
  try {
    await test();
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(`FAIL ${name}: ${error.stack || error.message}`);
    process.exitCode = 1;
  }
}

async function main() {
  await run("posted expense detail returns to the two-section expense query", async () => {
    const source = "/expenses?period=2026-Q2&q=supplier";
    const h = createHarness({initial: source, postedIds: [Q2_ID]}); await h.flush();
    assert.equal(h.element("#expense-search").value, "supplier");
    await h.click(`/expenses/${Q2_ID}?period=2026-Q2&returnTo=${encodeURIComponent(source)}`);
    assert.match(h.app.innerHTML, /expense-detail/);
    assert.doesNotMatch(h.app.innerHTML, /review-form|review-primary-button/);
    assert.equal(h.requests.some(url => url.startsWith("/api/review/work-item")), false);
    await h.click(source);
    assert.equal(h.element("#expense-search").value, "supplier");
    assert.match(h.element("#expense-results").innerHTML, /expenses-amortization/);
    assert.match(h.element("#expense-results").innerHTML, /expenses-purchase/);
  });

  await run("expense refresh respects the active route and open status help", async () => {
    const h = createHarness({initial: "/expenses?period=2026-Q2"}); await h.flush();
    assert.equal(h.inspect().view, "expenses");
    assert.match(h.element("#expense-results").innerHTML, /expenses-amortization/);
    assert.match(h.element("#expense-results").innerHTML, /expenses-purchase/);
    assert.equal(h.intervals.length, 1);
    assert.equal(h.intervals[0].delay, 60000);
    h.context.document.visibilityState = "visible";
    const countReads = () => h.requests.filter(url => url.startsWith("/api/expenses?")).length;
    const initialReads = countReads();
    h.intervals[0].callback(); await h.flush();
    assert.equal(countReads(), initialReads + 1);
    h.element("#status-help-dialog").open = true;
    h.intervals[0].callback(); await h.flush();
    assert.equal(countReads(), initialReads + 1);
    h.element("#status-help-dialog").open = false;
    await h.click("/review?period=2026-Q2");
    h.intervals[0].callback(); await h.flush();
    assert.equal(countReads(), initialReads + 1);
    assert.equal(h.inspect().view, "review");
  });

  await run("posted legacy review uses the read-only expense page before work-item or draft", async () => {
    const h = createHarness({initial: `/review/${Q2_ID}?period=2026-Q3`, postedIds: [Q2_ID]});
    await h.flush();
    assert.equal(h.window.location.pathname, `/expenses/${Q2_ID}`);
    assert.equal(h.window.location.search, "?period=2026-Q2");
    assert.match(h.app.innerHTML, /Synthetic saved note/);
    assert.doesNotMatch(h.app.innerHTML, /review-form|review-primary-button/);
    assert.equal(h.requests.some(url => url.startsWith("/api/review/work-item")), false);
    assert.equal(h.storage.has(`autonomo.review-draft:${Q2_ID}`), false);
  });
  await run("R1 table opens a raw-ID detail route with list context", async () => {
    const h = createHarness(); await h.flush();
    const html = vm.runInContext(`reviewTransactionTable([{transaction_id: ${JSON.stringify(Q2_ID)}, lifecycle_status: "needs_review", transaction_date: "2026-05-15"}])`, h.context);
    assert.match(html, new RegExp(`href="/review/${Q2_ID}\\?period=2026-Q2"`));
    assert.doesNotMatch(html, /transaction%3A/);
  });

  await run("R2 card back link returns to the Q2 list", async () => {
    const h = createHarness(); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    const backHref = /class="secondary-button review-back-link" href="([^"]+)"/.exec(h.app.innerHTML)?.[1];
    assert.equal(backHref, "/review?period=2026-Q2");
    await h.click(backHref);
    const view = h.inspect();
    assert.equal(h.window.location.pathname + h.window.location.search, "/review?period=2026-Q2");
    assert.equal(view.selectedReviewId, null);
    assert.equal(view.workItem, null);
    assert.match(view.html, /review-shell/);
    assert.doesNotMatch(view.html, /review-workspace/);
    assert.equal(view.disabled, false);
  });

  await run("status explanations render through the real helper during Q2 list/card navigation", async () => {
    const h = createHarness(); await h.flush();
    assert.match(h.app.innerHTML, /status-details-button/);
    assert.match(h.app.innerHTML, /Требуется проверка/);
    const statusHelpId = /data-status-help="([^"]+)"/.exec(h.app.innerHTML)?.[1];
    assert.ok(statusHelpId);
    const initialUrl = h.window.location.pathname + h.window.location.search;
    const initialHistoryLength = h.window.history.length;
    await h.clickStatusHelp(statusHelpId);
    assert.equal(h.element("#status-help-dialog").open, true);
    assert.equal(h.window.history.state.accountingHelp, statusHelpId);
    assert.equal(h.window.history.length, initialHistoryLength + 1);
    const requestsBeforeHelpHistory = h.requests.length;
    h.window.history.back(); await h.flush();
    assert.equal(h.element("#status-help-dialog").open, false);
    assert.equal(h.window.location.pathname + h.window.location.search, initialUrl);
    h.window.history.forward(); await h.flush();
    assert.equal(h.element("#status-help-dialog").open, true);
    assert.equal(h.window.history.state.accountingHelp, statusHelpId);
    assert.equal(h.window.location.pathname + h.window.location.search, initialUrl);
    assert.equal(h.requests.length, requestsBeforeHelpHistory);
    assert.equal(h.window.history.length, initialHistoryLength + 1);
    vm.runInContext("void renderCurrentView()", h.context);
    await h.flush();
    assert.equal(h.element("#status-help-dialog").open, false);
    assert.equal(h.window.history.state.accountingHelp, undefined);
    assert.equal(h.window.location.pathname + h.window.location.search, initialUrl);
    assert.match(h.app.innerHTML, /review-shell/);
    assert.equal(h.app.getAttribute("aria-busy"), "false");
    h.window.history.back(); await h.flush();
    assert.equal(h.element("#status-help-dialog").open, false);
    assert.equal(h.window.location.pathname + h.window.location.search, initialUrl);
    await h.click(`/review/${Q2_ID}`);
    assert.equal(h.inspect().period, "2026-Q2");
    assert.match(h.app.innerHTML, /review-workspace/);
    assert.match(h.app.innerHTML, /status-details-button/);
    const backHref = /class="secondary-button review-back-link" href="([^"]+)"/.exec(h.app.innerHTML)?.[1];
    await h.click(backHref);
    assert.equal(h.inspect().period, "2026-Q2");
    assert.match(h.app.innerHTML, /review-shell/);
    assert.match(h.app.innerHTML, /status-details-button/);
  });

  await run("R6 cold Q2 deep link uses packet period without Q3 list requests", async () => {
    const h = createHarness({initial: `/review/${Q2_ID}`}); await h.flush();
    const view = h.inspect();
    assert.equal(view.period, "2026-Q2");
    assert.match(view.html, /review-workspace/);
    assert.match(view.html, /period=2026-Q2/);
    assert.equal(h.requests.some((url) => url.includes("period=2026-Q3")), false);
    const reloaded = createHarness({initial: `/review/${Q2_ID}`}); await reloaded.flush();
    assert.equal(reloaded.inspect().period, "2026-Q2");
  });

  await run("R3 menu review from a card returns to the list for the card period", async () => {
    const h = createHarness(); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.clickNav("review");
    const view = h.inspect();
    assert.equal(view.selectedReviewId, null);
    assert.match(view.html, /review-shell/);
    assert.equal(view.period, "2026-Q2");
  });

  await run("R4 browser back and forward restore list and card without new history", async () => {
    const h = createHarness(); await h.flush();
    const initialLength = h.window.history.length;
    await h.click(`/review/${Q2_ID}`);
    await h.click("/review?period=2026-Q2");
    const afterNavigationLength = h.window.history.length;
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().selectedReviewId, `transaction:${Q2_ID}`);
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().selectedReviewId, null);
    h.window.history.forward(); await h.flush();
    assert.equal(h.inspect().selectedReviewId, `transaction:${Q2_ID}`);
    assert.equal(h.window.history.length, afterNavigationLength);
    assert.equal(afterNavigationLength, initialLength + 2);
  });

  await run("R5 mixed Q2/Q3 history restores each route's screen and period", async () => {
    const h = createHarness(); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.click("/review?period=2026-Q2");
    h.periodSelect.value = "2026-Q3";
    for (const callback of h.periodSelect.listeners.get("change") || []) callback({target: h.periodSelect});
    await h.flush();
    await h.click(`/review/${Q3_ID}`);
    assert.deepEqual(h.inspect().period, "2026-Q3");
    assert.equal(h.inspect().selectedReviewId, `transaction:${Q3_ID}`);
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().period, "2026-Q3");
    assert.equal(h.inspect().selectedReviewId, null);
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().period, "2026-Q2");
    assert.equal(h.inspect().selectedReviewId, `transaction:${Q2_ID}`);
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().period, "2026-Q2");
    assert.equal(h.inspect().selectedReviewId, null);
  });

  await run("native modified SPA clicks remain browser-owned", async () => {
    const h = createHarness(); await h.flush();
    const before = h.window.location.pathname + h.window.location.search;
    await h.click(`/review/${Q2_ID}`, {ctrlKey: true});
    assert.equal(h.window.location.pathname + h.window.location.search, before);
    assert.equal(h.inspect().selectedReviewId, null);
  });

  await run("R7 detail query cannot override its packet period", async () => {
    const h = createHarness({initial: `/review/${Q2_ID}?period=2026-Q3#synthetic-proof`});
    const initialLength = h.window.history.length;
    await h.flush();
    assert.equal(h.inspect().period, "2026-Q2");
    assert.equal(h.window.location.pathname + h.window.location.search + h.window.location.hash, `/review/${Q2_ID}?period=2026-Q2#synthetic-proof`);
    assert.equal(h.window.history.length, initialLength);
  });

  await run("R8 an explicit Q2 SPA list href survives a Q3 state", async () => {
    const h = createHarness({initial: "/review?period=2026-Q3"}); await h.flush();
    await h.click("/review?period=2026-Q2");
    assert.equal(h.inspect().period, "2026-Q2");
    assert.equal(h.window.location.pathname + h.window.location.search, "/review?period=2026-Q2");
  });

  await run("R9 invalid list period falls back and normalizes its current history entry", async () => {
    const h = createHarness({initial: "/review?period=not-a-quarter"});
    const initialLength = h.window.history.length;
    await h.flush();
    assert.equal(h.inspect().period, "2026-Q3");
    assert.equal(h.window.location.pathname + h.window.location.search, "/review?period=2026-Q3");
    assert.equal(h.window.history.length, initialLength);
  });

  await run("R10 stale detail success cannot restore a card after navigation to income", async () => {
    const h = createHarness({deferred: true}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.click("/income?period=2026-Q2");
    for (const pending of h.deferredRequests) pending.resolve(response(pending.payload));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.view, "income");
    assert.equal(view.selectedReviewId, null);
    assert.equal(view.workItem, null);
    assert.doesNotMatch(view.html, /review-workspace/);
    assert.match(view.html, /transactions-table/);
  });

  await run("R10 stale detail failure cannot replace the current list", async () => {
    const h = createHarness({deferred: true}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.click("/review?period=2026-Q2");
    for (const pending of h.deferredRequests) pending.reject(new Error("synthetic delayed failure"));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.selectedReviewId, null);
    assert.equal(view.workItem, null);
    assert.match(view.html, /review-shell/);
    assert.doesNotMatch(view.html, /synthetic delayed failure/);
  });

  await run("R11 stale facts-only refresh cannot restore a card after returning to the list", async () => {
    const h = createHarness({deferDetailAt: 2}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    const refresh = h.element("#review-refresh-button");
    for (const callback of refresh.listeners.get("click") || []) callback({target: refresh});
    await h.flush();
    await h.click("/review?period=2026-Q2");
    assert.equal(h.deferredRequests.length, 1);
    h.deferredRequests[0].resolve(response(h.deferredRequests[0].payload));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.selectedReviewId, null);
    assert.equal(view.workItem, null);
    assert.match(view.html, /review-shell/);
  });

  await run("R11 reverse-order same-card refresh keeps only the latest facts and draft", async () => {
    const h = createHarness({
      deferDetailCalls: [2, 3],
      workItemFactory: (id, call) => packet(id, "2026-Q2", `snapshot-refresh-${call}`),
    });
    await h.flush();
    await h.click(`/review/${Q2_ID}`);
    const refresh = h.element("#review-refresh-button");
    for (const callback of refresh.listeners.get("click") || []) callback({target: refresh});
    await h.flush();
    for (const callback of refresh.listeners.get("click") || []) callback({target: refresh});
    await h.flush();
    assert.equal(h.deferredRequests.length, 2);
    h.deferredRequests[1].resolve(response(h.deferredRequests[1].payload));
    await h.flush();
    h.deferredRequests[0].reject(new Error("synthetic old refresh failure"));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.workItem.packet.snapshot_hash, "snapshot-refresh-3");
    assert.equal(view.period, "2026-Q2");
    assert.equal(view.workItem.packet.review_id, `transaction:${Q2_ID}`);
    assert.doesNotMatch(view.html, /synthetic old refresh failure/);
    assert.match(h.storage.get(`autonomo.review-draft:${Q2_ID}`), /snapshot-refresh-3/);
  });

  await run("R10 reverse-order Q2/Q3 details keep Q3 state and do not write Q2 draft", async () => {
    const h = createHarness({deferred: true}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.click(`/review/${Q3_ID}`);
    assert.equal(h.deferredRequests.length, 2);
    h.deferredRequests[1].resolve(response(h.deferredRequests[1].payload));
    await h.flush();
    h.deferredRequests[0].resolve(response(h.deferredRequests[0].payload));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.period, "2026-Q3");
    assert.equal(view.workItem.packet.review_id, `transaction:${Q3_ID}`);
    assert.equal(h.storage.has(`autonomo.review-draft:${Q2_ID}`), false);
    assert.match(h.storage.get(`autonomo.review-draft:${Q3_ID}`), new RegExp(Q3_ID));
  });

  await run("R12 missing period and mismatched IDs fail without persisting a wrong card", async () => {
    for (const mutate of [
      (payload) => { delete payload.packet.state.period.period_key; },
      (payload) => { payload.packet.state.transaction.transaction_id = Q3_ID; },
      (payload) => { payload.packet.review_id = `transaction:${Q3_ID}`; },
    ]) {
      const h = createHarness({workItemFactory: (id) => {
        const payload = packet(id, "2026-Q2"); mutate(payload); return payload;
      }});
      await h.flush();
      await h.click(`/review/${Q2_ID}`);
      const view = h.inspect();
      assert.equal(view.workItem, null);
      assert.match(view.html, /Не удалось проверить операцию|Invalid review packet/);
      assert.equal(h.storage.has(`autonomo.review-draft:${Q2_ID}`), false);
      assert.equal(h.storage.has(`autonomo.review-draft:${Q3_ID}`), false);
    }
  });

  await run("R10 unknown route invalidates a pending detail", async () => {
    const h = createHarness({deferred: true}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    await h.applyExternalLocation("/synthetic-unknown-route");
    assert.equal(h.app.getAttribute("aria-busy"), "false");
    assert.equal(h.element("#status-help-dialog").open, false);
    h.deferredRequests[0].resolve(response(h.deferredRequests[0].payload));
    await h.flush();
    const view = h.inspect();
    assert.equal(view.selectedReviewId, null);
    assert.equal(view.workItem, null);
    assert.match(view.html, /Страница не найдена|Unknown section/);
    assert.equal(h.storage.has(`autonomo.review-draft:${Q2_ID}`), false);
    assert.equal(h.app.getAttribute("aria-busy"), "false");
    assert.equal(h.element("#status-help-dialog").open, false);
  });

  await run("R11 preserves each transaction draft across list navigation without transfer", async () => {
    const h = createHarness(); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    const q2Draft = h.storage.get(`autonomo.review-draft:${Q2_ID}`);
    assert.match(q2Draft, new RegExp(Q2_ID));
    await h.click("/review?period=2026-Q2");
    assert.equal(h.storage.get(`autonomo.review-draft:${Q2_ID}`), q2Draft);
    await h.click(`/review/${Q3_ID}`);
    assert.match(h.storage.get(`autonomo.review-draft:${Q3_ID}`), new RegExp(Q3_ID));
    assert.doesNotMatch(h.storage.get(`autonomo.review-draft:${Q3_ID}`), new RegExp(Q2_ID));
  });

  await run("R5 period selector replaces its history entry and popstate restores it", async () => {
    const h = createHarness(); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    h.window.history.back(); await h.flush();
    const before = h.window.history.length;
    h.periodSelect.value = "2026-Q3";
    for (const callback of h.periodSelect.listeners.get("change") || []) callback({target: h.periodSelect});
    await h.flush();
    assert.equal(h.window.history.length, before);
    assert.equal(h.window.location.pathname + h.window.location.search, "/review?period=2026-Q3");
    h.window.history.forward(); await h.flush();
    assert.equal(h.inspect().period, "2026-Q2");
    assert.equal(h.inspect().selectedReviewId, `transaction:${Q2_ID}`);
    h.window.history.back(); await h.flush();
    assert.equal(h.inspect().period, "2026-Q3");
    assert.equal(h.inspect().selectedReviewId, null);
  });

  await run("R12 a failed second detail cannot leave the first card under the new URL", async () => {
    const missingId = "33333333-3333-4333-8333-333333333333";
    const h = createHarness({failDetailIds: [missingId]}); await h.flush();
    await h.click(`/review/${Q2_ID}`);
    assert.match(h.inspect().html, /review-workspace/);
    await h.click(`/review/${missingId}`);
    const view = h.inspect();
    assert.equal(view.selectedReviewId, `transaction:${missingId}`);
    assert.equal(view.workItem, null);
    assert.match(view.html, /synthetic unknown review/);
    assert.doesNotMatch(view.html, /snapshot-11111111/);
  });
}

void main();
