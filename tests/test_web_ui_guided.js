const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) {
    throw new Error(`Could not find ${name} in app.js`);
  }
  const signatureEnd = source.indexOf(")", start);
  let braceIndex = source.indexOf("{", signatureEnd);
  let depth = 0;
  for (let index = braceIndex; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return source.slice(start, index + 1);
      }
    }
  }
  throw new Error(`Could not parse ${name} in app.js`);
}

const appPath = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js");
const source = fs.readFileSync(appPath, "utf8");
const REVIEW_UUID = "701d8ede-ceee-4db6-89c7-028bf5841abc";

const context = {
  Map,
  Set,
  Number,
  String,
  Array,
  Boolean,
  JSON,
  URLSearchParams,
  state: {locale: "ru", period: "2026-Q3", review: {}},
  messages: {
    ru: {"review.issueAutoResolveHint": "Закроется автоматически: {labels}"},
    en: {},
  },
  escapeHtml: (value) =>
    String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char])),
  ROUTE_VIEWS: {
    "/dashboard": "dashboard",
    "/income": "income",
    "/expenses": "expenses",
    "/review": "review",
    "/assets": "assets",
    "/taxes": "taxes",
    "/contacts": "contacts",
  },
  REVIEW_DETAIL_RE: /^\/review\/([0-9a-fA-F-]{32,36})$/,
  EXPENSE_DETAIL_RE: /^\/expenses\/([0-9a-fA-F-]{32,36})$/,
  PERIOD_ROUTE_PATHS: new Set(["/dashboard", "/income", "/expenses", "/review", "/assets", "/taxes"]),
  REVIEW_TABS: ["queue", "posting", "documents"],
};

[
  "t",
  "parseRoute",
  "routePathFor",
  "buildRouteUrl",
  "reviewTabFromQuery",
  "autoResolveCoveredIssues",
  "mapConfirmErrorToQuestion",
  "buildConfirmFxSpec",
  "questionAnswerMap",
  "readDecisionFieldValue",
  "fxChoiceNeeded",
  "initialFxChoice",
].forEach((name) => {
  vm.runInNewContext(`${extractFunction(source, name)}; this.${name} = ${name};`, context);
});

// Routing
assert.deepEqual(
  Object.fromEntries(Object.entries(context.parseRoute("/dashboard"))),
  {view: "dashboard", reviewId: null},
);
assert.deepEqual(
  Object.fromEntries(Object.entries(context.parseRoute("/review"))),
  {view: "review", reviewId: null},
);
assert.deepEqual(
  Object.fromEntries(Object.entries(context.parseRoute(`/review/${REVIEW_UUID}`))),
  {view: "review", reviewId: REVIEW_UUID},
);
assert.equal(context.parseRoute("/nope"), null);
assert.equal(context.parseRoute("/"), null);
assert.equal(
  context.routePathFor("review", REVIEW_UUID),
  `/review/${REVIEW_UUID}`,
);
assert.equal(context.routePathFor("review"), "/review");
assert.equal(context.buildRouteUrl("dashboard"), "/dashboard?period=2026-Q3");
assert.equal(context.buildRouteUrl("assets"), "/assets?period=2026-Q3");
assert.equal(context.buildRouteUrl("taxes", {period: "2026-Q2"}), "/taxes?period=2026-Q2");
assert.equal(
  context.buildRouteUrl("review", {reviewId: REVIEW_UUID}),
  `/review/${REVIEW_UUID}?period=2026-Q3`,
);
assert.equal(context.buildRouteUrl("review", {reviewId: `transaction:${REVIEW_UUID}`, period: "2026-Q2"}), `/review/${REVIEW_UUID}?period=2026-Q2`);

// Review tabs: the default queue tab stays out of the URL, other tabs are
// carried in ?tab=, detail routes never carry one, and the active tab
// travels with programmatic navigations such as a period switch.
assert.equal(context.buildRouteUrl("review"), "/review?period=2026-Q3");
assert.equal(context.buildRouteUrl("review", {tab: "posting"}), "/review?period=2026-Q3&tab=posting");
assert.equal(context.buildRouteUrl("review", {tab: "queue"}), "/review?period=2026-Q3");
assert.equal(
  context.buildRouteUrl("review", {reviewId: REVIEW_UUID, tab: "posting"}),
  `/review/${REVIEW_UUID}?period=2026-Q3`,
);
context.state.review.activeTab = "documents";
assert.equal(context.buildRouteUrl("review"), "/review?period=2026-Q3&tab=documents");
context.state.review.activeTab = "queue";
assert.equal(context.buildRouteUrl("review"), "/review?period=2026-Q3");
delete context.state.review.activeTab;
assert.equal(context.reviewTabFromQuery(new URLSearchParams("tab=documents")), "documents");
assert.equal(context.reviewTabFromQuery(new URLSearchParams("tab=POSTING")), "posting");
assert.equal(context.reviewTabFromQuery(new URLSearchParams("tab=bogus")), "queue");
assert.equal(context.reviewTabFromQuery(new URLSearchParams("")), "queue");

// Guided issues
const packet = {
  decision: {
    issue_resolutions: [
      {issue_id: "tax-issue", action: "", reason: ""},
      {issue_id: "manual-issue", action: "keep_open", reason: ""},
    ],
  },
  state: {
    issues: [
      {validation_issue_id: "manual-issue", issue_code: "manual_review", message: "two"},
      {validation_issue_id: "tax-issue", issue_code: "transaction_tax_review", message: "one"},
    ],
  },
};
context.autoResolveCoveredIssues(
  packet,
  {
    transaction_tax_review: ["business_purpose", "tax_code"],
    counterparty_tax_profile_review: ["counterparty_country"],
  },
  {business_purpose: true, tax_code: true},
);
assert.equal(packet.decision.issue_resolutions[0].action, "resolve");
assert.equal(packet.decision.issue_resolutions[0].reason, "Закроется автоматически: 2");
assert.equal(packet.decision.issue_resolutions[1].action, "keep_open");
assert.equal(packet.decision.issue_resolutions[1].reason, "");

// Error mapping
assert.equal(context.mapConfirmErrorToQuestion("business_purpose is required"), "business_purpose");
assert.equal(context.mapConfirmErrorToQuestion("FX rate must be positive"), "fx_rate");
assert.equal(context.mapConfirmErrorToQuestion("something else entirely"), "general");

// FX spec building
const suggestion = {
  status: "exact",
  rate_date: "2026-08-18",
  eur_per_unit: "0.9216",
  source_reference: "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
  raw_observation: '{"currency":"USD","date":"2026-08-18","value":"1.0850"}',
  raw_observation_hash: "abc123",
};
const ecbSpec = context.buildConfirmFxSpec({mode: "ecb"}, suggestion);
assert.equal(ecbSpec.rate_date, "2026-08-18");
assert.equal(ecbSpec.rate, "0.9216");
assert.equal(ecbSpec.rate_source, "ecb");
assert.equal(ecbSpec.source_reference, null);
assert.equal(ecbSpec.raw_observation, null);
assert.equal(ecbSpec.raw_observation_hash, null);
assert.equal(ecbSpec.supersedes_rate_id, null);
const settlementSpec = context.buildConfirmFxSpec(
  {mode: "settlement", rate: "0.91", rateDate: "2026-08-18", sourceReference: "Bank advice 4"},
  null,
);
assert.equal(settlementSpec.rate_source, "actual_settlement");
assert.equal(settlementSpec.rate, "0.91");
assert.equal(settlementSpec.supersedes_rate_id, null);
assert.equal(context.buildConfirmFxSpec(null, suggestion), null);
assert.equal(
  context.buildConfirmFxSpec({mode: "settlement", rate: "", rateDate: "", sourceReference: ""}, null),
  null,
);

// Question coverage and FX necessity
const answered = context.questionAnswerMap(
  {
    business_purpose: "Software",
    tax_treatment: {tax_code: "domestic_input", deductible_irpf_minor: 100},
    document_valid: true,
    reason: "ok",
  },
  {counterparty: {country_code: "ES"}},
  {mode: "ecb"},
);
assert.equal(answered.business_purpose, true);
assert.equal(answered.tax_code, true);
assert.equal(answered.fx_rate, true);
assert.equal(answered.counterparty_country, true);

assert.equal(
  context.fxChoiceNeeded({original_currency: "USD", fx_rate_id: null}),
  true,
);
assert.equal(
  context.fxChoiceNeeded({original_currency: "EUR", fx_rate_id: null}),
  false,
);
assert.equal(
  context.fxChoiceNeeded({original_currency: "USD", fx_rate_id: "fx-1"}),
  false,
);
assert.equal(context.initialFxChoice(suggestion).mode, "ecb");
assert.equal(context.initialFxChoice({status: "unavailable"}), null);
assert.equal(context.initialFxChoice(null), null);

console.log("guided web ui checks OK");

// False is an explicit reviewed IVA choice; blank remains unknown.
assert.equal(context.readDecisionFieldValue({dataset: {valueType: "nullable-boolean"}, value: "false", type: "select-one"}), false);
assert.equal(context.readDecisionFieldValue({dataset: {valueType: "nullable-boolean"}, value: "", type: "select-one"}), null);
assert.equal(context.questionAnswerMap({tax_treatment: {vat_investment_good: false}}, null, {}).vat_investment_good, true);
assert.equal(context.questionAnswerMap({tax_treatment: {vat_investment_good: null}}, null, {}).vat_investment_good, false);
assert.equal(context.mapConfirmErrorToQuestion("Expense review requires an explicit vat_investment_good boolean"), "vat_investment_good");
assert.ok(source.includes('data-decision-path="tax_treatment.vat_investment_good" data-value-type="nullable-boolean"'));
