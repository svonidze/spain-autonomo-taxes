"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appPath = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js");
const source = fs.readFileSync(appPath, "utf8");

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`Could not find ${name}`);
  const signatureEnd = source.indexOf(")", start);
  const brace = source.indexOf("{", signatureEnd);
  let depth = 0;
  for (let index = brace; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Could not extract ${name}`);
}

const context = vm.createContext({
  t(key, variables = {}) {
    return Object.entries(variables).reduce(
      (text, [name, value]) => `${text}|${name}=${value}`,
      key
    );
  },
  formatDate: (value) => `date:${value}`,
  formatMinorEur: (value) => `minor:${value}`,
  escapeHtml: (value) => String(value),
});

for (const name of [
  "taxSettlementLabel",
  "taxStatusTone",
  "taxHeadlineModel",
  "taxSummaryFacts",
]) {
  vm.runInContext(extractFunction(name), context);
}

const planned = vm.runInContext(`taxHeadlineModel(
  {phase: "current"},
  {settlement_status: "planned", total_payable_minor: 320427, total_confirmed_paid_minor: 0,
   outstanding_minor: 320427, overpaid_minor: 0, calculated_as_of: "2026-09-03"}
)`, context);
assert.equal(planned.label, "taxes.headlinePlanned");
assert.equal(planned.amount, 320427);
assert.ok(planned.detail.includes("date:2026-09-03"));

const unconfirmed = vm.runInContext(`taxHeadlineModel(
  {phase: "past"},
  {settlement_status: "payment_unconfirmed", total_payable_minor: 263912,
   total_confirmed_paid_minor: 0, outstanding_minor: 263912, overpaid_minor: 0,
   calculation_source: "filed"}
)`, context);
assert.equal(unconfirmed.label, "taxes.headlineConfirmed");
assert.equal(unconfirmed.amount, 0);
assert.ok(unconfirmed.detail.includes("taxes.unconfirmedDetail"));
assert.ok(unconfirmed.detail.includes("amount=minor:263912"));

const filedValuesMissing = vm.runInContext(`taxHeadlineModel(
  {phase: "past"},
  {settlement_status: "payment_unconfirmed", total_payable_minor: 263912,
   total_confirmed_paid_minor: 0, outstanding_minor: 263912, overpaid_minor: 0,
   calculation_source: "preview"}
)`, context);
assert.ok(filedValuesMissing.detail.includes("taxes.previewUnconfirmedDetail"));

const partial = vm.runInContext(`taxHeadlineModel(
  {phase: "past"},
  {settlement_status: "partially_paid", total_payable_minor: 263912,
   total_confirmed_paid_minor: 100000, outstanding_minor: 163912, overpaid_minor: 0}
)`, context);
assert.equal(partial.amount, 100000);
assert.ok(partial.detail.includes("amount=minor:163912"));
assert.equal(partial.tone, "pending");

const overpaid = vm.runInContext(`taxHeadlineModel(
  {phase: "past"},
  {settlement_status: "overpaid", total_payable_minor: 263912,
   total_confirmed_paid_minor: 300000, outstanding_minor: 0, overpaid_minor: 36088}
)`, context);
assert.equal(overpaid.amount, 300000);
assert.ok(overpaid.detail.includes("amount=minor:36088"));
assert.equal(overpaid.tone, "attention");

const future = vm.runInContext(`taxHeadlineModel(
  {phase: "future"},
  {settlement_status: "not_started", total_payable_minor: null, total_confirmed_paid_minor: 0}
)`, context);
assert.equal(future.label, "taxes.headlineNotStarted");
assert.equal(future.amount, null);

const facts = vm.runInContext(`taxSummaryFacts(
  {total_payable_minor: 263912, total_confirmed_paid_minor: 100000,
   outstanding_minor: 163912, overpaid_minor: 0}, "past"
)`, context);
assert.ok(facts.includes("minor:263912"));
assert.ok(facts.includes("minor:100000"));
assert.ok(facts.includes("minor:163912"));

const renderStart = source.indexOf("async function renderTaxes(");
const renderEnd = source.indexOf("async function renderContacts(", renderStart);
const renderTaxes = source.slice(renderStart, renderEnd);
assert.ok(renderTaxes.includes('class="tax-hero'));
assert.ok(renderTaxes.includes('taxFormCard("130"'));
assert.ok(renderTaxes.includes('taxFormCard("303"'));
assert.ok(!renderTaxes.includes('class="tax-layout"'));
assert.ok(source.includes('class="tax-calculation-details"'));

console.log(JSON.stringify({ok: true}));
