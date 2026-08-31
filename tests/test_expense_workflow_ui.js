const assert = require("node:assert/strict");
const fs = require("node:fs");
const {randomUUID} = require("node:crypto");
const values = new Map();
global.window = {
  crypto: {randomUUID},
  sessionStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  },
};
const workflow = require("../src/autonomo_taxes/web_ui/expense-workflow.js");
const draft = JSON.parse(fs.readFileSync(0, "utf8"));

class Container {
  constructor() { this.html = ""; this.nodes = new Map(); }
  set innerHTML(html) {
    this.html = html;
    this.nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map((match) => ["#" + match[1], {
      listeners: {},
      addEventListener(type, listener) { this.listeners[type] = listener; },
    }]));
    for (const match of html.matchAll(/data-recognize="([^"]+)"/g)) {
      this.nodes.set("recognize:" + match[1], {dataset: {recognize: match[1]}, listeners: {},
        addEventListener(type, listener) {this.listeners[type] = listener;}});
    }
  }
  get innerHTML() { return this.html; }
  querySelector(selector) { return this.nodes.get(selector === '[role="status"]' ? '#wf-schedule-status' : selector) ?? null; }
  querySelectorAll(selector) { return ['button','[data-recognize]'].includes(selector) ? [...this.nodes.values()].filter(node => node.dataset?.recognize) : []; }
  async fire(selector, type) {
    const node = this.querySelector(selector);
    assert(node?.listeners[type], `Missing action ${selector}`);
    await node.listeners[type]({preventDefault() {}});
    // Form submit deliberately detaches its async task from the DOM event.
    for (let i = 0; i < 8; i++) await new Promise(setImmediate);
  }
}

async function main() {
  const first = workflow.pendingRequest("expense", "preview-a", 1);
  assert.deepEqual(workflow.pendingRequest("expense", "preview-a", 1), first);
  assert.notEqual(workflow.pendingRequest("expense", "preview-b", 1).request_id, first.request_id);
  assert.notEqual(workflow.pendingRequest("expense", "preview-a", 2).request_id, first.request_id);

  const container = new Container();
  const confirmations = [];
  let followUps = 0;
  const api = async (url, request) => {
    if (url === "/api/counterparties") return [];
    if (!request) return draft;
    if (url.endsWith("/save")) return {...draft, draft_version: draft.draft_version + 1};
    if (url.endsWith("/preview")) return {preview_token: "checked-result", supplier: "Synthetic supplier", period: "2026-Q3", gross_minor: 12100, currency: "EUR", deductible_irpf_minor: 10000, deductible_vat_minor: 2100, future_depreciation_minor: 0, schedule: []};
    if (url.endsWith("/confirm")) {
      confirmations.push(JSON.parse(request.body));
      if (confirmations.length === 1) throw new Error("Simulated lost response");
      return {posted: true, transaction_id: draft.transaction_id, follow_up_pending: true};
    }
    if (url.endsWith("/follow-up")) { followUps++; return {posted: true, follow_up_pending: false}; }
    throw new Error(`Unexpected request ${url}`);
  };
  const options = {container, api, transactionId: draft.transaction_id, isActive: () => true, onPosted() {}};
  await workflow.open(options);
  await container.fire("#wf-form", "submit");
  await container.fire("#wf-confirm", "click");
  assert.match(container.html, /Simulated lost response/);
  await container.fire("#wf-confirm", "click");
  assert.deepEqual(confirmations[0], confirmations[1], "Retry must reuse the exact request");
  assert.match(container.html, /Проведено в учёте/);
  assert.equal(container.querySelector("#wf-confirm"), null, "Posted state must not offer reposting");
  await container.fire("#wf-follow-up", "click");
  assert.equal(followUps, 1);
  assert.equal(confirmations.length, 2);
  assert.equal(container.querySelector("#wf-follow-up"), null);

  let legacy = 0;
  await workflow.open({...options, api: async (url) => url === "/api/counterparties" ? [] : {...draft, source: {...draft.source, assets: [{asset_id: "synthetic-existing"}]}}, onLegacy: async () => {legacy++;}});
  assert.equal(legacy, 1, "An existing asset must retain its original review route");

  const scheduleContainer = new Container();
  let recognitionCount = 0;
  await workflow.showSchedule({container: scheduleContainer, assetId: "synthetic-asset", isActive: () => true,
    api: async (url, request) => {
      if (request) {recognitionCount++; return {posted: true};}
      return {native: true, rows: [{amortization_entry_id: "synthetic-row",period_key: "2026-Q2", amount_minor: 623, can_post: true, row_version: 1}]};
    },
    onPosted: async () => {throw new Error("Asset table reload unavailable");},
  });
  await scheduleContainer.fire("recognize:synthetic-row", "click");
  assert.equal(recognitionCount, 1);
  assert.equal(scheduleContainer.querySelector("recognize:synthetic-row").disabled, true);
  assert.match(scheduleContainer.querySelector('[role="status"]').textContent, /Амортизация проведена/);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
