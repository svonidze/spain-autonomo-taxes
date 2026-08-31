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

console.log(JSON.stringify({ok: true}));
