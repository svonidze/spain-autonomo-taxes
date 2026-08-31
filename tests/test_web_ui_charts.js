"use strict";

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const chartsPath = path.join(
  __dirname,
  "..",
  "src",
  "autonomo_taxes",
  "web_ui",
  "charts.js"
);
const source = fs.readFileSync(chartsPath, "utf8");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const AutonomoCharts = sandbox.AutonomoCharts;
assert.ok(AutonomoCharts, "charts.js must register the AutonomoCharts namespace");

const eur = (value) => `${(value / 100).toFixed(2)}`;

// charts.js runs in a separate vm realm, so its arrays/objects carry foreign
// prototypes; clone through JSON before deepStrictEqual against literals.
const plain = (value) => JSON.parse(JSON.stringify(value));

function cartesianSpec(overrides) {
  return Object.assign(
    {
      chartId: "test-chart",
      title: "Test",
      buckets: ["2026-01", "2026-02"],
      formatValue: eur,
      series: [
        {
          key: "income",
          label: "Income",
          kind: "bar",
          stack: "income",
          tone: "info",
          pattern: "solid",
          values: [10000, 20000],
        },
      ],
    },
    overrides
  );
}

function geometryOnly(scene) {
  return scene.marks.map((mark) => ({
    kind: mark.kind,
    x: mark.x,
    y: mark.y,
    width: mark.width,
    height: mark.height,
    points: mark.points,
    cx: mark.cx,
    cy: mark.cy,
  }));
}

// Positive-only bars sit on the bottom baseline.
{
  const scene = AutonomoCharts.buildCartesianScene(cartesianSpec({}));
  assert.strictEqual(scene.empty, false);
  const rects = scene.marks.filter((mark) => mark.kind === "rect");
  assert.strictEqual(rects.length, 2);
  assert.ok(rects[1].height > rects[0].height, "larger value draws taller");
  rects.forEach((mark) => {
    assert.ok(
      Math.abs(mark.y + mark.height - scene.baselineY) <= 2,
      "positive bars anchor at the zero baseline"
    );
  });
}

// Mixed signs keep the zero baseline strictly inside the plot.
{
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "net",
          label: "Net",
          kind: "bar",
          stack: "net",
          tone: "info",
          pattern: "solid",
          values: [10000, -5000],
        },
      ],
    })
  );
  const [positive, negative] = scene.marks.filter((mark) => mark.kind === "rect");
  assert.ok(scene.baselineY > 14 && scene.baselineY < 214);
  assert.ok(positive.y + positive.height <= scene.baselineY + 2);
  assert.ok(negative.y >= scene.baselineY - 2);
}

// All-zero data is a real chart (known zeros), not an empty state.
{
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "income",
          label: "Income",
          kind: "bar",
          stack: "income",
          tone: "info",
          pattern: "solid",
          values: [0, 0],
        },
      ],
    })
  );
  assert.strictEqual(scene.empty, false);
  scene.marks
    .filter((mark) => mark.kind === "rect")
    .forEach((mark) => assert.strictEqual(mark.height, 0));
}

// Null values split a line into segments; singleton segments become dots.
{
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      buckets: ["a", "b", "c"],
      series: [
        {
          key: "trend",
          label: "Trend",
          kind: "line",
          tone: "accent",
          pattern: "solid",
          values: [100, null, 300],
        },
      ],
    })
  );
  const points = scene.marks.filter((mark) => mark.kind === "point");
  assert.strictEqual(points.length, 2, "two isolated values become two dots");

  const segmented = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      buckets: ["a", "b", "c", "d", "e"],
      series: [
        {
          key: "trend",
          label: "Trend",
          kind: "line",
          tone: "accent",
          pattern: "dashed",
          values: [100, 200, null, 400, 500],
        },
      ],
    })
  );
  const polylines = segmented.marks.filter((mark) => mark.kind === "polyline");
  assert.strictEqual(polylines.length, 2, "a null gap splits the polyline");
  polylines.forEach((mark) => {
    assert.ok(mark.className.includes("chart-line-dashed"));
    assert.strictEqual(mark.points.split(" ").length, 2);
  });
}

// Forecast patterns keep their tone but change fill treatment.
{
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "actual",
          label: "Actual",
          kind: "bar",
          stack: "expense",
          tone: "warning",
          pattern: "solid",
          values: [1000, null],
        },
        {
          key: "forecast",
          label: "Forecast",
          kind: "bar",
          stack: "expense",
          tone: "warning",
          pattern: "hatched",
          values: [null, 2000],
        },
        {
          key: "outlined",
          label: "Outlined",
          kind: "bar",
          stack: "future",
          tone: "warning",
          pattern: "outline",
          values: [null, 500],
        },
      ],
    })
  );
  const byKey = Object.fromEntries(
    scene.marks.map((mark) => [mark.seriesKey, mark])
  );
  assert.strictEqual(byKey.actual.hatchTone, null);
  assert.strictEqual(byKey.forecast.hatchTone, "warning");
  assert.deepStrictEqual(plain(scene.hatchTones), ["warning"]);
  assert.ok(byKey.outlined.className.includes("chart-mark-outline"));
}

// Scenes are deterministic: same spec, same scene.
{
  const first = AutonomoCharts.buildCartesianScene(cartesianSpec({}));
  const second = AutonomoCharts.buildCartesianScene(cartesianSpec({}));
  assert.deepStrictEqual(plain(first), plain(second));
}

// Locale changes rebuild labels without moving geometry.
{
  const russian = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "income",
          label: "Доход",
          kind: "bar",
          stack: "income",
          tone: "info",
          pattern: "solid",
          values: [10000, 20000],
        },
      ],
    })
  );
  const english = AutonomoCharts.buildCartesianScene(cartesianSpec({}));
  assert.deepStrictEqual(plain(geometryOnly(russian)), plain(geometryOnly(english)));
  assert.notStrictEqual(russian.legend[0].label, english.legend[0].label);
}

// Hostile labels stay inert data in the scene model.
{
  const hostile = "<img src=x onerror=alert(1)>";
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "income",
          label: hostile,
          kind: "bar",
          stack: "income",
          tone: "info",
          pattern: "solid",
          values: [100, 200],
        },
      ],
    })
  );
  assert.strictEqual(scene.legend[0].label, hostile);
  assert.ok(scene.marks[0].title.includes(hostile));
}

// Horizontal bars: stable legend, running totals, negatives clamp to zero width.
{
  const scene = AutonomoCharts.buildHorizontalBarsScene({
    chartId: "expenses",
    title: "Expenses",
    formatValue: eur,
    rows: [
      {
        key: "G45",
        label: "Supplies",
        segments: [
          {key: "deductible", label: "Deductible", tone: "warning", value: 3000},
          {key: "rest", label: "Non-deductible", tone: "muted", value: 1000},
        ],
      },
      {
        key: "unclassified",
        label: "Unclassified",
        segments: [
          {key: "deductible", label: "Deductible", tone: "warning", value: -500},
          {key: "rest", label: "Non-deductible", tone: "muted", value: 2000},
        ],
      },
    ],
  });
  assert.strictEqual(scene.empty, false);
  assert.deepStrictEqual(
    plain(scene.legend.map((entry) => entry.key)),
    ["deductible", "rest"]
  );
  const negative = scene.marks.find(
    (mark) => mark.seriesKey === "unclassified-deductible"
  );
  assert.strictEqual(negative.width, 0);
  assert.strictEqual(scene.rowLabels.length, 2);
}

// Bullet: a missing measure is flagged, not drawn.
{
  const spec = {
    chartId: "reserve",
    title: "Reserve",
    formatValue: eur,
    ranges: [
      {key: "recommended", label: "Recommended", tone: "muted", value: 36391},
      {key: "required", label: "Required", tone: "warning", value: 26391},
    ],
    measure: {key: "available", label: "Available", tone: "accent", value: null},
  };
  const scene = AutonomoCharts.buildBulletScene(spec);
  assert.strictEqual(scene.empty, false);
  assert.strictEqual(scene.measureUnavailable, true);
  assert.strictEqual(scene.marks.length, 2);

  const withMeasure = AutonomoCharts.buildBulletScene(
    Object.assign({}, spec, {
      measure: {key: "available", label: "Available", tone: "accent", value: 40000},
    })
  );
  assert.strictEqual(withMeasure.measureUnavailable, false);
  assert.strictEqual(withMeasure.marks.length, 3);
}

// Empty specs surface the empty state instead of drawing.
{
  const scene = AutonomoCharts.buildCartesianScene(
    cartesianSpec({
      series: [
        {
          key: "income",
          label: "Income",
          kind: "bar",
          stack: "income",
          tone: "info",
          pattern: "solid",
          values: [null, null],
        },
      ],
    })
  );
  assert.strictEqual(scene.empty, true);
  assert.strictEqual(scene.marks.length, 0);
}

// Width-true rendering: scenes adopt spec.width inside the [280, 1600] band.
{
  const narrow = AutonomoCharts.buildCartesianScene(cartesianSpec({width: 320}));
  assert.strictEqual(narrow.viewBox.width, 320);
  assert.strictEqual(narrow.grid[0].x2, 304);
  assert.strictEqual(
    AutonomoCharts.buildCartesianScene(cartesianSpec({width: 10000})).viewBox.width,
    1600
  );
  assert.strictEqual(
    AutonomoCharts.buildCartesianScene(cartesianSpec({width: 100})).viewBox.width,
    280
  );
  assert.strictEqual(
    AutonomoCharts.buildCartesianScene(cartesianSpec({width: "wide"})).viewBox.width,
    640
  );
  assert.strictEqual(
    AutonomoCharts.buildCartesianScene(cartesianSpec({})).viewBox.width,
    640
  );
}

// Narrow monthly charts thin bucket labels deterministically; marks stay complete.
{
  const buckets = [];
  for (let month = 1; month <= 12; month += 1) {
    buckets.push(`2026-${String(month).padStart(2, "0")}`);
  }
  const monthlySeries = [
    {
      key: "income",
      label: "Income",
      kind: "bar",
      stack: "income",
      tone: "info",
      pattern: "solid",
      values: buckets.map((_, index) => (index + 1) * 100),
    },
  ];
  const wide = AutonomoCharts.buildCartesianScene(
    cartesianSpec({buckets, series: monthlySeries})
  );
  assert.strictEqual(wide.bucketLabels.length, 12);
  const narrow = AutonomoCharts.buildCartesianScene(
    cartesianSpec({width: 320, buckets, series: monthlySeries})
  );
  assert.strictEqual(narrow.bucketLabels.length, 6);
  assert.strictEqual(
    narrow.marks.filter((mark) => mark.kind === "rect").length,
    12
  );
}

// Horizontal bars: the label column keeps 150 at the default width and
// shrinks toward 90 on narrow slots.
{
  const rows = [
    {
      key: "G45",
      label: "Supplies",
      segments: [
        {key: "deductible", label: "Deductible", tone: "warning", value: 3000},
      ],
    },
  ];
  const spec = {chartId: "expenses", title: "Expenses", formatValue: eur, rows};
  const wide = AutonomoCharts.buildHorizontalBarsScene(spec);
  assert.strictEqual(wide.viewBox.width, 640);
  assert.strictEqual(wide.rowLabels[0].x, 150);
  const narrow = AutonomoCharts.buildHorizontalBarsScene(
    Object.assign({}, spec, {width: 320})
  );
  assert.strictEqual(narrow.viewBox.width, 320);
  assert.strictEqual(narrow.rowLabels[0].x, 90);
}

// Bullet scenes honour the requested width too.
{
  const scene = AutonomoCharts.buildBulletScene({
    chartId: "reserve",
    title: "Reserve",
    formatValue: eur,
    width: 320,
    ranges: [
      {key: "required", label: "Required", tone: "warning", value: 26391},
    ],
    measure: null,
  });
  assert.strictEqual(scene.viewBox.width, 320);
}

// ---- app.js spec builders (extracted with stubbed i18n) --------------------

function extractFunction(appSource, name) {
  const marker = `function ${name}(`;
  const start = appSource.indexOf(marker);
  if (start === -1) throw new Error(`Could not find ${name} in app.js`);
  const signatureEnd = appSource.indexOf(")", start);
  const braceIndex = appSource.indexOf("{", signatureEnd);
  let depth = 0;
  for (let index = braceIndex; index < appSource.length; index += 1) {
    const char = appSource[index];
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return appSource.slice(start, index + 1);
    }
  }
  throw new Error(`Could not parse ${name} in app.js`);
}

const appPath = path.join(__dirname, "..", "src", "autonomo_taxes", "web_ui", "app.js");
const appSource = fs.readFileSync(appPath, "utf8");
const builderContext = {
  Object,
  Array,
  Number,
  String,
  Boolean,
  JSON,
  Math,
  Date,
  Intl,
  t: (key) => key,
  eur: (value) => String(value),
  intlLocale: () => "en",
  statusLabel: (value) => `status:${value}`,
};
vm.createContext(builderContext);
[
  "formatMinorEur",
  "chartMonthLabel",
  "chartSpecBase",
  "chartHostWidth",
  "reviewQueueTotal",
  "transactionsEmptyMessage",
  "taxChartEmptyMessage",
  "buildBusinessResultSpec",
  "buildTaxDueSpec",
  "buildIvaPositionSpec",
  "buildReserveSpec",
  "buildCumulativeNetSpec",
  "buildYearComparisonSpec",
  "buildExpenseStructureSpec",
  "buildReviewAgingSpec",
  "buildCounterpartySpec",
  "buildAmortizationSpec",
].forEach((name) => vm.runInContext(extractFunction(appSource, name), builderContext));

// chartHostWidth measures defensively and subtracts the figure padding.
{
  assert.strictEqual(
    builderContext.chartHostWidth({getBoundingClientRect: () => ({width: 371})}),
    343
  );
  assert.strictEqual(builderContext.chartHostWidth({clientWidth: 348}), 320);
  assert.strictEqual(builderContext.chartHostWidth({}), 0);
  assert.strictEqual(builderContext.chartHostWidth(null), 0);
}

function analyticsFixture(overrides) {
  return Object.assign(
    {
      quality: {missing_fx_transaction_count: 0},
      datasets: {
        business_result: {
          monthly: {
            buckets: ["2026-01", "2026-02"],
            actual: {
              income_base_minor: [10000, null],
              deductible_expense_minor: [5000, null],
            },
            approved_unposted: {
              income_base_minor: [0, 0],
              deductible_expense_minor: [0, 2000],
            },
            approved_future: {
              income_base_minor: [0, 3000],
              deductible_expense_minor: [0, 0],
            },
          },
        },
        cumulative_net: {
          buckets: ["2026-01", "2026-02"],
          actual_minor: [5000, null],
          projected_minor: [5000, 6000],
        },
        quarterly_tax_due: {
          points: [
            {
              period_key: "2026-Q1",
              modelo130: {result_minor: 26391, payable_minor: 26391, source: "filed"},
              modelo303: {result_minor: -4074, payable_minor: 0, source: "filed"},
            },
            {
              period_key: "2026-Q2",
              modelo130: {result_minor: null, payable_minor: null, source: "filed_without_values"},
              modelo303: {result_minor: null, payable_minor: null, source: "unavailable"},
            },
          ],
        },
        iva_position: {
          points: [
            {
              period_key: "2026-Q1",
              output_vat_minor: 10000,
              deductible_input_vat_minor: 14074,
              result_minor: -4074,
              source: "filed",
            },
          ],
        },
        reserve_bullet: {
          status: "not_checked",
          required_tax_minor: 26391,
          recommended_reserve_minor: 36391,
          available_minor: null,
        },
        review_aging: {
          buckets: ["0-7", "8-30", "31-90", "90+"],
          counts: {received: [0, 0, 0, 0]},
        },
        ytd_comparison: {
          through_month: 8,
          current_year: {
            year: 2026,
            taxable_income_minor: 80000,
            deductible_expense_minor: 20000,
            net_minor: 60000,
          },
          previous_year: {
            year: 2025,
            taxable_income_minor: null,
            deductible_expense_minor: null,
            net_minor: null,
          },
        },
        expense_structure: {
          buckets: [
            {concept: "G45", gross_minor: 30000, deductible_minor: 20000, non_deductible_minor: 10000},
            {concept: "unclassified", gross_minor: 5000, deductible_minor: 0, non_deductible_minor: 5000},
          ],
        },
        counterparty_concentration: {
          top: [
            {counterparty_id: "cp-1", name: "Client One", income_minor: 90000},
          ],
          other_minor: 15000,
        },
        amortization: {
          includes: "include_in_books_only",
          points: [
            {period_key: "2026-Q1", total_minor: 6500, assets: []},
            {period_key: "2026-Q2", total_minor: 6500, assets: []},
          ],
        },
      },
    },
    overrides
  );
}

// Business spec: six series, forecast encoded by pattern, month labels localized.
{
  const spec = builderContext.buildBusinessResultSpec(analyticsFixture());
  assert.strictEqual(spec.series.length, 6);
  assert.deepStrictEqual(plain(spec.buckets), ["Jan", "Feb"]);
  const patterns = spec.series.map((entry) => entry.pattern);
  assert.ok(patterns.includes("hatched") && patterns.includes("outline"));
  const scene = AutonomoCharts.buildCartesianScene(spec);
  assert.strictEqual(scene.empty, false);
}

// Tax-due spec: positive payables only, filed-without-values drives the empty copy.
{
  const spec = builderContext.buildTaxDueSpec(analyticsFixture());
  assert.deepStrictEqual(plain(spec.buckets), ["2026-Q1", "2026-Q2"]);
  assert.deepStrictEqual(plain(spec.series[0].values), [26391, null]);
  assert.deepStrictEqual(plain(spec.series[1].values), [0, null]);
  assert.strictEqual(spec.emptyMessage, "charts.empty.filedWithoutValues");
}

// Reserve spec: not_checked keeps the measure unavailable; blocked empties the chart.
{
  const spec = builderContext.buildReserveSpec(analyticsFixture());
  assert.strictEqual(spec.ranges.length, 2);
  assert.strictEqual(spec.measure.value, null);
  assert.strictEqual(spec.unavailableMessage, "charts.reserve.notChecked");
  const scene = AutonomoCharts.buildBulletScene(spec);
  assert.strictEqual(scene.measureUnavailable, true);

  const blocked = builderContext.buildReserveSpec(
    analyticsFixture({
      datasets: Object.assign({}, analyticsFixture().datasets, {
        reserve_bullet: {status: "calculation_blocked", required_tax_minor: null},
      }),
    })
  );
  assert.strictEqual(blocked.emptyMessage, "charts.reserve.blocked");
  assert.strictEqual(blocked.ranges.length, 0);
}

// Cumulative spec: projected dashed line under the actual solid line.
{
  const spec = builderContext.buildCumulativeNetSpec(analyticsFixture());
  assert.strictEqual(spec.series[0].pattern, "dashed");
  assert.strictEqual(spec.series[1].pattern, "solid");
  assert.strictEqual(spec.series[0].tone, spec.series[1].tone);
}

// Year comparison keeps null previous-year values as gaps, previous year hatched.
{
  const spec = builderContext.buildYearComparisonSpec(analyticsFixture());
  assert.strictEqual(spec.series[0].pattern, "hatched");
  assert.deepStrictEqual(plain(spec.series[0].values), [null, null, null]);
  assert.deepStrictEqual(plain(spec.series[1].values), [80000, 20000, 60000]);
  assert.ok(spec.series[0].label.includes("2025"));
}

// Expense structure keeps API order and translates the unclassified bucket.
{
  const spec = builderContext.buildExpenseStructureSpec(analyticsFixture());
  assert.deepStrictEqual(
    plain(spec.rows.map((row) => row.label)),
    ["G45", "charts.expenses.unclassified"]
  );
  assert.strictEqual(spec.rows[0].segments[1].pattern, "hatched");
  const scene = AutonomoCharts.buildHorizontalBarsScene(spec);
  assert.strictEqual(scene.empty, false);
}

// Review aging: an empty queue collapses to the empty state; counts are not money.
{
  const emptySpec = builderContext.buildReviewAgingSpec(analyticsFixture());
  assert.strictEqual(emptySpec.rows.length, 0);
  const busy = builderContext.buildReviewAgingSpec(
    analyticsFixture({
      datasets: Object.assign({}, analyticsFixture().datasets, {
        review_aging: {
          buckets: ["0-7", "8-30", "31-90", "90+"],
          counts: {
            received: [1, 0, 0, 0],
            approved_unposted: [0, 0, 2, 0],
          },
        },
      }),
    })
  );
  assert.strictEqual(busy.rows.length, 4);
  assert.strictEqual(busy.formatValue(7), "7");
  const overdue = busy.rows[2].segments.find(
    (segment) => segment.key === "approved_unposted"
  );
  assert.strictEqual(overdue.value, 2);
  assert.strictEqual(overdue.tone, "danger");
}

// Counterparties: the long tail folds into a hatched "other" row.
{
  const spec = builderContext.buildCounterpartySpec(analyticsFixture());
  assert.strictEqual(spec.rows.length, 2);
  assert.strictEqual(spec.rows[1].key, "other");
  assert.strictEqual(spec.rows[1].segments[0].pattern, "hatched");
}

// Amortization: quarter totals with the include-in-books note.
{
  const spec = builderContext.buildAmortizationSpec(analyticsFixture());
  assert.strictEqual(spec.note, "charts.amortization.note");
  assert.deepStrictEqual(plain(spec.buckets), ["2026-Q1", "2026-Q2"]);
  assert.deepStrictEqual(plain(spec.series[0].values), [6500, 6500]);
}

// View chart mounts guard against superseded renders (stale-response races).
{
  const mountSource = extractFunction(appSource, "mountViewAnalyticsChart");
  assert.ok(
    mountSource.includes("currentRenderGeneration"),
    "mountViewAnalyticsChart must capture and re-check the render generation"
  );
}

// Empty-state taxonomy: FX gaps beat the generic message; review queue is named.
{
  const fx = builderContext.transactionsEmptyMessage(
    analyticsFixture({quality: {missing_fx_transaction_count: 2}})
  );
  assert.strictEqual(fx, "charts.empty.missingFx");
  const unreviewed = builderContext.transactionsEmptyMessage(
    analyticsFixture({
      datasets: Object.assign({}, analyticsFixture().datasets, {
        review_aging: {counts: {needs_review: [1, 0, 0, 0]}},
      }),
    })
  );
  assert.strictEqual(unreviewed, "charts.empty.onlyUnreviewed");
  const none = builderContext.transactionsEmptyMessage(analyticsFixture());
  assert.strictEqual(none, "charts.empty.noTransactions");
}

console.log(JSON.stringify({ok: true}));
