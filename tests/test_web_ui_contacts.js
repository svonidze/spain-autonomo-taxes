const __uiCore = require('./legacy_core.cjs');
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../src/autonomo_taxes/web_ui/app.js"), "utf8");
const nodes = new Map();
let focused = null;
function node(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {
    innerHTML: "", textContent: "", value: "", hidden: false, disabled: false, style: {}, dataset: {},
    offsetWidth: 220, offsetHeight: 56, isConnected: true, scrollLeft: 0,
    setAttribute(key, value) { this[key] = value; },
    removeAttribute(key) { delete this[key]; },
    focus() { focused = this; },
    close() { this.open = false; }, showModal() { this.open = true; },
    addEventListener(key, action) { this[key + "Handler"] = action; },
    querySelector: node, querySelectorAll: () => [], closest: () => null,
  });
  return nodes.get(selector);
}
const window = {
  location: {origin: "http://localhost", pathname: "/contacts", search: "", hash: "", href: "http://localhost/contacts"},
  history: {
    state: null,
    replaceState(state, title, url) { this.state = state; if (url) setLocation(url); },
    pushState(state, title, url) { this.state = state; setLocation(url); },
  },
  innerWidth: 375, innerHeight: 812, scrollX: 0, scrollY: 400,
  scrollTo(position) { this.restoredPosition = position; },
  getSelection: () => ({toString: () => ""}), confirm: () => true,
};
const document = {querySelector: node, querySelectorAll: () => [], activeElement: null};
function setLocation(url) {
  const next = new URL(url, window.location.origin);
  Object.assign(window.location, {pathname: next.pathname, search: next.search, hash: next.hash, href: next.href});
}
const AccountingHelp = {
  cell: () => "<span>Status</span>", term: () => "", labelTables() {}, beforeRender() {}, setLocale() {},
};
const ctx = vm.createContext(__uiCore.prepare({window, document, URL, URLSearchParams, console, AccountingHelp}));
vm.runInContext(source.slice(0, source.lastIndexOf("\nif (hasDOM) {")), __uiCore.prepare(ctx));
vm.runInContext(`this.h = {
  state, route: parseRoute, url: contactUrl, back: safeReturnUrl, cell: counterpartyNameCell,
  trigger: counterpartyMenuTrigger, position: contactMenuPosition, allowed: counterpartyRowClickAllowed,
  toggle: toggleCounterpartyMenu, closeMenu: closeCounterpartyMenu,
  load: loadContactOperations, history: loadContactHistory, render: renderContactDetail,
  openEditor: openCounterpartyNameEditor, submit: submitCounterpartyName,
  put: row => counterpartyRowsById.set(row.counterparty_id, row),
  generation: value => { currentRenderGeneration = value; },
  getEditor: () => counterpartyNameEditor,
  remember: rememberContactsListPosition, restore: restoreContactsListPosition,
  navigate: navigateToUrl, routeFromLocation: applyRouteFromLocation,
  applyExpensePeriod: applyDetailPeriod, buildUrl: buildRouteUrl,
  closeEditor: closeCounterpartyNameEditor, closeOutside: closeCounterpartyMenuFromOutside,
  operationMarkup: contactOperationMarkup,
};`, __uiCore.prepare(ctx));
const h = ctx.h;
const id = "d1029c71-3ed3-4c7b-8b42-971b8f312222";
const party = {counterparty_id: id, display_name: "Synthetic <Party>", row_version: 1, legal_form: "legal_entity"};
const fresh = () => ({
  id, period: "", data: {counterparty: {...party}, periods: []}, rows: [],
  nextOffset: 0, total: null, hasMore: false, busy: false, error: false, history: null, historyRequest: 0,
});
const plain = value => JSON.parse(JSON.stringify(value));
const op = index => ({
  transaction_id: String(index), transaction_date: "2026-08-01", entry_type: "income",
  period_key: "2026-Q3", amount_eur: "0.00", ui_context: {}, description: "Synthetic",
});
const page = (rows, offset, hasMore) => ({rows, next_offset: offset + rows.length, matching_count: 61, has_more: hasMore});
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

async function main() {
  assert.equal(h.url(id, "2026-Q2"), "/contacts/" + id + "?period=2026-Q2");
  assert.equal(h.route("/contacts/" + id).view, "contact-detail");
  assert.equal(h.back("/contacts/" + id + "?period=2026-Q2", "2026-Q3"), "/contacts/" + id + "?period=2026-Q2");
  assert.equal(h.back("/contacts/" + id, "2026-Q3"), "/contacts/" + id);
  for (const unsafe of ["//example.invalid", "/contacts/" + id + "?returnTo=https://example.invalid",
    "/contacts/" + id + "?period=bad", "/contacts/" + id + "?period=2026-Q1&period=2026-Q2"]) {
    assert.equal(h.back(unsafe, "2026-Q3"), "/expenses?period=2026-Q3");
  }
  assert.match(h.cell(party), /data-spa/);
  assert.match(h.cell(party), /&lt;Party&gt;/);
  assert.doesNotMatch(h.cell({...party, name_is_manual: true}), /button|Исправлено вручную|Исправить имя/);
  assert.match(h.trigger(party), /aria-haspopup="menu"/);
  const pos = h.position({right: 365, top: 790, bottom: 812}, 220, 56, 375, 812);
  assert.deepEqual(plain(pos), {left: 145, top: 730});
  assert.equal(h.allowed({button: 0, target: {closest: () => null}}), true);
  assert.equal(h.allowed({button: 0, ctrlKey: true, target: {closest: () => null}}), false);
  assert.equal(h.allowed({button: 0, target: {closest: () => ({})}}), false);
  window.getSelection = () => ({toString: () => "selected text"});
  assert.equal(h.allowed({button: 0, target: {closest: () => null}}), false);
  window.getSelection = () => ({toString: () => ""});

  h.state.view = "contacts";
  h.put(party);
  const trigger = node("trigger");
  trigger.dataset.counterpartyMenu = id;
  trigger.getBoundingClientRect = () => ({right: 365, top: 790, bottom: 812});
  h.toggle(trigger);
  assert.equal(trigger["aria-expanded"], "true");
  assert.equal(node("#counterparty-actions-menu").style.top, "730px");
  h.closeMenu();
  assert.equal(trigger["aria-expanded"], "false");
  assert.equal(focused, trigger);
  trigger.contains = () => false;
  h.toggle(trigger);
  h.closeOutside({closest: () => null});
  assert.equal(focused, trigger);
  h.toggle(trigger);
  const otherInput = node("other-input");
  otherInput.focus();
  h.closeOutside({closest: selector => selector.startsWith("a,") ? otherInput : null});
  assert.equal(focused, otherInput);
  node("#contacts-list-wrap").scrollLeft = 55;
  h.remember();
  h.restore();
  assert.equal(window.restoredPosition.top, 400);
  assert.equal(node("#contacts-list-wrap").scrollLeft, 55);

  h.state.view = "contact-detail";
  h.generation(1);
  const detail = fresh();
  h.state.contactDetail = detail;
  let resolve;
  let calls = 0;
  ctx.fetchJSON = () => { calls++; return new Promise(done => { resolve = done; }); };
  const pending = h.load(detail, 1);
  await h.load(detail, 1);
  assert.equal(calls, 1);
  resolve(page(Array.from({length: 50}, (_, i) => op(i)), 0, true));
  await pending;
  assert.equal(detail.rows.length, 50);
  ctx.fetchJSON = async () => { throw new Error("network"); };
  await h.load(detail, 1);
  assert.equal(detail.rows.length, 50);
  assert.equal(detail.error, true);
  ctx.fetchJSON = async url => {
    assert.match(url, /offset=50/);
    return page(Array.from({length: 11}, (_, i) => op(50 + i)), 50, false);
  };
  await h.load(detail, 1);
  assert.equal(detail.rows.length, 61);
  assert.equal(detail.hasMore, false);
  assert.equal(detail.error, false);
  assert.equal((h.operationMarkup(op(0), detail).match(/data-label=/g) || []).length, 6);

  const stale = fresh();
  h.state.contactDetail = stale;
  ctx.fetchJSON = () => new Promise(done => { resolve = done; });
  const old = h.load(stale, 1);
  h.state.contactDetail = fresh();
  node("#contact-operations-results").innerHTML = "new card";
  resolve(page([op(1)], 0, false));
  await old;
  assert.equal(node("#contact-operations-results").innerHTML, "new card");

  // A direct detail URL needs no list-cache entry to open the existing editor.
  const direct = fresh();
  direct.id = "d1029c71-3ed3-4c7b-8b42-971b8f313333";
  direct.data.counterparty = {...party, counterparty_id: direct.id};
  h.state.contactDetail = direct;
  ctx.fetchJSON = async () => ({changes: []});
  h.openEditor(direct.id, trigger, direct.data.counterparty);
  await settle();
  assert.equal(h.getEditor().row.counterparty_id, direct.id);
  node("#counterparty-name-input").value = "Synthetic Corrected";
  ctx.showToast = () => {};
  ctx.fetchJSON = async () => ({
    ...direct.data.counterparty, display_name: "Synthetic Corrected", row_version: 2, changed: true,
  });
  await h.submit({preventDefault() {}});
  assert.equal(h.state.view, "contact-detail");
  assert.equal(direct.data.counterparty.display_name, "Synthetic Corrected");
  assert.match(node("#contact-identity").innerHTML, /Synthetic Corrected/);
  assert.equal(window.location.pathname, "/contacts");

  // Exercise real route functions: card filters and expense excursions must
  // not replace the original global period used by the other sections.
  ctx.renderCurrentView = async () => {};
  ctx.applyViewState = () => {};
  ctx.closePostingConfirmDialog = () => {};
  h.state.bootstrap = {default_period: "2026-Q3", periods: [
    {period_key: "2026-Q2"}, {period_key: "2026-Q3"},
  ]};
  h.state.view = "contacts";
  h.state.period = "2026-Q3";
  setLocation("/contacts");
  const cardUrl = h.url(id, "2026-Q2");
  h.navigate(cardUrl);
  assert.equal(h.state.period, "2026-Q3");
  assert.equal(h.state.contactDetail.period, "2026-Q2");
  h.navigate("/expenses/" + id + "?period=2026-Q2&returnTo=" + encodeURIComponent(cardUrl));
  assert.equal(window.history.state.contactGlobalPeriod, "2026-Q3");
  // Emulate a fresh app boot on the expense while retaining browser history.
  h.state.contactGlobalPeriod = null;
  h.state.view = "dashboard";
  h.state.period = "2026-Q3";
  h.routeFromLocation();
  assert.equal(h.state.contactGlobalPeriod, "2026-Q3");
  h.applyExpensePeriod({period: {period_key: "2026-Q2", status: "open"}}, "expense-detail", id);
  assert.equal(h.state.period, "2026-Q2");
  h.navigate(cardUrl);
  assert.equal(h.state.period, "2026-Q3");
  assert.equal(h.state.contactDetail.period, "2026-Q2");
  assert.equal(h.buildUrl("income"), "/income?period=2026-Q3");

  // Refusing a browser Back keeps both the draft and original route. A busy
  // save also blocks navigation rather than discarding its eventual result.
  ctx.fetchJSON = async () => ({changes: []});
  h.openEditor(id, trigger, party);
  await settle();
  node("#counterparty-name-input").value = "Unsaved synthetic name";
  let confirmations = 0;
  window.confirm = () => { confirmations++; return false; };
  setLocation("/contacts");
  assert.equal(h.routeFromLocation(), false);
  assert.equal(confirmations, 1);
  assert.equal(window.location.pathname, "/contacts/" + id);
  assert.equal(node("#counterparty-name-input").value, "Unsaved synthetic name");
  h.getEditor().busy = true;
  setLocation("/contacts");
  assert.equal(h.routeFromLocation(), false);
  assert.equal(confirmations, 1);
  assert.equal(h.state.view, "contact-detail");
  h.getEditor().busy = false;
  window.confirm = () => true;
  assert.equal(h.closeEditor(), true);
  console.log("Contact navigation, menus, pagination and stale-response tests passed");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
