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
  if (signatureEnd === -1) {
    throw new Error(`Could not parse signature for ${name} in app.js`);
  }
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
const context = {
  Map,
  Number,
  String,
  Array,
  Boolean,
  Set,
  state: {period: "2026-Q3"},
};

[
  "integerValue",
  "dedupeMessages",
  "reviewIdToTransactionId",
  "normalizePostingReasons",
  "normalizePostingItem",
  "normalizePostingItems",
  "normalizePostingSummary",
  "normalizePostingPreview",
  "postingItemMap",
  "normalizePostingResult",
  "buildPostReadyItems",
].forEach((name) => {
  vm.runInNewContext(`${extractFunction(source, name)}; this.${name} = ${name};`, context);
});

const preview = context.normalizePostingPreview({
  period: "2026-Q3",
  period_status: "open",
  generated_at: "2026-08-04T15:30:00Z",
  summary: {
    approved_count: 4,
    ready_count: 1,
    deferred_count: 1,
    blocked_count: 2,
    ready_total_eur: "121.00",
    cleanup_count: 2,
    cleanup_blocked_count: 1,
  },
  ready: [
    {
      review_id: "transaction:abc",
      row_version: 7,
      transaction_date: "2026-07-02",
      entry_type: "expense",
      description: "Ready row",
      amount_eur: "121.00",
      reasons: [{code: "cleanup_apply", message: "Cleanup will run"}],
      cleanup_applies: true,
    },
  ],
  deferred: [
    {
      review_id: "transaction:def",
      expected_row_version: 5,
      reasons: [{code: "future_date", message: "Posting date is in the future"}],
    },
  ],
  blocked: [
    {
      review_id: "transaction:ghi",
      row_version: 9,
      blockers: [{code: "cleanup_blocked", message: "Cleanup is blocked"}],
      cleanup_blocked: true,
    },
  ],
});

assert.equal(preview.summary.cleanupCount, 2);
assert.equal(preview.summary.cleanupBlockedCount, 1);
assert.equal(preview.ready[0].expectedRowVersion, 7);
assert.equal(preview.deferred[0].expectedRowVersion, 5);
assert.equal(preview.blocked[0].expectedRowVersion, 9);
assert.equal(preview.ready[0].message, "Cleanup will run");
assert.equal(preview.blocked[0].message, "Cleanup is blocked");

assert.deepEqual(JSON.parse(JSON.stringify(context.buildPostReadyItems(preview.ready))), [
  {
    transaction_id: "abc",
    expected_row_version: 7,
  },
]);

const readyRows = preview.ready;
const normalizedResult = context.normalizePostingResult(
  {
    status: "partial",
    posted_count: 1,
    results: [
      {
        review_id: "transaction:abc",
        status: "posted",
      },
    ],
  },
  readyRows,
);

assert.equal(normalizedResult.summary.postedCount, 1);
assert.equal(normalizedResult.results[0].rowStatus, "posted");
assert.equal(normalizedResult.results[0].reviewId, "transaction:abc");
