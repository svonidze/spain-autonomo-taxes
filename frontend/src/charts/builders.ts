import {formatMessage, localeTag, messageIds, type Locale} from '../core/i18n.ts';
import {eur} from '../core/format.ts';
import type {Analytics, ChartSpec, TaxPoint} from './types.ts';

export function createChartBuilders(locale: Locale) {
  const t = (key: string) => formatMessage(key, {}, locale);
  const intlLocale = () => localeTag(locale);
  const statusLabel = (status: string) => t(`statuses.labels.${status}`);
function formatMinorEur(minor: number) {
  return eur(minor / 100, locale);
}

function formatChartPercent(ratio: number) {
  return new Intl.NumberFormat(intlLocale(), {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(ratio);
}

function expenseConceptPresentation(concept: string) {
  const raw = String(concept || "").trim();
  if (!raw || raw.toLowerCase() === "unclassified") {
    return {label: t("charts.expenses.unclassified"), secondaryLabel: ""};
  }
  const code = raw.toUpperCase();
  const labelKey = `aeat.expenseConcept.${code}`;
  return {
    label: messageIds.includes(labelKey) ? t(labelKey) : t("charts.expenses.unknownConcept"),
    secondaryLabel: code,
  };
}

function chartMonthLabel(bucket: string) {
  const parsed = new Date(`${bucket}-01T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(bucket);
  return new Intl.DateTimeFormat(intlLocale(), {month: "short"}).format(parsed);
}

function chartSpecBase(chartId: string, titleKey: string, ariaKey: string, emptyMessage: string): ChartSpec {
  return {
    chartId,
    title: t(titleKey),
    ariaLabel: t(ariaKey),
    tableLabel: t("charts.table"),
    formatValue: formatMinorEur,
    emptyMessage,
  };
}

function reviewQueueTotal(analytics: Analytics) {
  const counts = (analytics.datasets.review_aging || {}).counts || {};
  return Object.values(counts).reduce(
    (total, values) => total + values.reduce((sum, value) => sum + value, 0),
    0
  );
}

function transactionsEmptyMessage(analytics: Analytics) {
  if ((analytics.quality?.missing_fx_transaction_count ?? 0) > 0) {
    return t("charts.empty.missingFx");
  }
  if (reviewQueueTotal(analytics) > 0) return t("charts.empty.onlyUnreviewed");
  return t("charts.empty.noTransactions");
}

function taxChartEmptyMessage(points: (TaxPoint & {modelo130?: TaxPoint; modelo303?: TaxPoint})[], fallbackKey: string) {
  const filedWithoutValues = points.some((point) =>
    [point.modelo130, point.modelo303, point].some(
      (entry) => entry && entry.source === "filed_without_values"
    )
  );
  return filedWithoutValues
    ? t("charts.empty.filedWithoutValues")
    : t(fallbackKey);
}

function buildBusinessResultSpec(analytics: Analytics) {
  const monthly = analytics.datasets.business_result.monthly;
  const spec = chartSpecBase(
    "business-result",
    "charts.business.title",
    "charts.business.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.bucket.month");
  spec.buckets = monthly.buckets.map(chartMonthLabel);
  spec.series = [
    {key: "income-actual", label: t("charts.business.incomeActual"), kind: "bar", stack: "income", tone: "info", pattern: "solid", values: monthly.actual.income_base_minor},
    {key: "income-backlog", label: t("charts.business.incomeBacklog"), kind: "bar", stack: "income", tone: "info", pattern: "hatched", values: monthly.approved_unposted.income_base_minor},
    {key: "income-forecast", label: t("charts.business.incomeForecast"), kind: "bar", stack: "income", tone: "info", pattern: "outline", values: monthly.approved_future.income_base_minor},
    {key: "expense-actual", label: t("charts.business.expenseActual"), kind: "bar", stack: "expense", tone: "warning", pattern: "solid", values: monthly.actual.deductible_expense_minor},
    {key: "expense-backlog", label: t("charts.business.expenseBacklog"), kind: "bar", stack: "expense", tone: "warning", pattern: "hatched", values: monthly.approved_unposted.deductible_expense_minor},
    {key: "expense-forecast", label: t("charts.business.expenseForecast"), kind: "bar", stack: "expense", tone: "warning", pattern: "outline", values: monthly.approved_future.deductible_expense_minor},
  ];
  return spec;
}

function buildTaxDueSpec(analytics: Analytics) {
  const points = analytics.datasets.quarterly_tax_due.points || [];
  const spec = chartSpecBase(
    "tax-due",
    "charts.taxDue.title",
    "charts.taxDue.aria",
    taxChartEmptyMessage(points, "charts.taxDue.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "m130", label: t("charts.taxDue.m130"), kind: "bar", stack: "m130", tone: "accent", pattern: "solid", values: points.map((point) => point.modelo130.payable_minor)},
    {key: "m303", label: t("charts.taxDue.m303"), kind: "bar", stack: "m303", tone: "warning", pattern: "solid", values: points.map((point) => point.modelo303.payable_minor)},
  ];
  return spec;
}

function buildIvaPositionSpec(analytics: Analytics) {
  const points = analytics.datasets.iva_position.points || [];
  const spec = chartSpecBase(
    "iva-position",
    "charts.iva.title",
    "charts.iva.aria",
    taxChartEmptyMessage(points, "charts.taxDue.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "output", label: t("charts.iva.output"), kind: "bar", stack: "output", tone: "info", pattern: "solid", values: points.map((point) => point.output_vat_minor)},
    {key: "input", label: t("charts.iva.input"), kind: "bar", stack: "input", tone: "warning", pattern: "solid", values: points.map((point) => point.deductible_input_vat_minor)},
    {key: "result", label: t("charts.iva.result"), kind: "line", tone: "accent", pattern: "solid", values: points.map((point) => point.result_minor)},
  ];
  return spec;
}

function buildReserveSpec(analytics: Analytics) {
  const reserve = analytics.datasets.reserve_bullet;
  const spec = chartSpecBase(
    "tax-reserve",
    "charts.reserve.title",
    "charts.reserve.aria",
    t("charts.reserve.notRequired")
  );
  spec.bucketLabel = "";
  spec.ranges = [];
  spec.measure = null;
  if (reserve.status === "calculation_blocked" || reserve.status === "unsupported_form") {
    spec.emptyMessage = t("charts.reserve.blocked");
    return spec;
  }
  if (!reserve.required_tax_minor) return spec;
  spec.ranges = [
    {key: "recommended", label: t("charts.reserve.recommended"), tone: "info", value: reserve.recommended_reserve_minor},
    {key: "required", label: t("charts.reserve.required"), tone: "warning", value: reserve.required_tax_minor},
  ];
  spec.measure = {
    key: "available",
    label: t("charts.reserve.available"),
    tone: "accent",
    value: reserve.available_minor,
  };
  spec.unavailableMessage = t("charts.reserve.notChecked");
  return spec;
}

function buildCumulativeNetSpec(analytics: Analytics) {
  const cumulative = analytics.datasets.cumulative_net;
  const spec = chartSpecBase(
    "cumulative-net",
    "charts.cumulative.title",
    "charts.cumulative.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.bucket.month");
  spec.buckets = cumulative.buckets.map(chartMonthLabel);
  spec.series = [
    {key: "net-projected", label: t("charts.cumulative.projected"), kind: "line", tone: "accent", pattern: "dashed", values: cumulative.projected_minor},
    {key: "net-actual", label: t("charts.cumulative.actual"), kind: "line", tone: "accent", pattern: "solid", values: cumulative.actual_minor},
  ];
  return spec;
}

function buildYearComparisonSpec(analytics: Analytics) {
  const comparison = analytics.datasets.ytd_comparison;
  const spec = chartSpecBase(
    "ytd-comparison",
    "charts.yoy.title",
    "charts.yoy.aria",
    t("charts.yoy.empty")
  );
  spec.bucketLabel = "";
  spec.buckets = [
    t("charts.yoy.income"),
    t("charts.yoy.deductible"),
    t("charts.yoy.net"),
  ];
  const previous = comparison.previous_year;
  const current = comparison.current_year;
  spec.series = [
    {key: "previous", label: `${t("charts.yoy.previousYear")} (${previous.year})`, kind: "bar", stack: "previous", tone: "warning", pattern: "hatched", values: [previous.taxable_income_minor, previous.deductible_expense_minor, previous.net_minor]},
    {key: "current", label: `${t("charts.yoy.currentYear")} (${current.year})`, kind: "bar", stack: "current", tone: "accent", pattern: "solid", values: [current.taxable_income_minor, current.deductible_expense_minor, current.net_minor]},
  ];
  return spec;
}

function buildExpenseStructureSpec(analytics: Analytics) {
  const buckets = analytics.datasets.expense_structure.buckets || [];
  const spec = chartSpecBase(
    "expense-structure",
    "charts.expenses.title",
    "charts.expenses.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.expenses.concept");
  spec.tableLabel = t("charts.expenses.table");
  spec.totalLabel = t("charts.expenses.total");
  spec.totalValueLabel = t("charts.expenses.totalValue");
  spec.shareUnavailableLabel = t("charts.expenses.shareUnavailable");
  spec.formatPercent = formatChartPercent;
  spec.showSegmentShares = true;
  spec.tableInitiallyOpen = true;
  spec.tablePrimaryOnNarrow = true;
  spec.rowLabelWidth = 280;
  spec.rowHeight = 42;
  spec.rows = buckets.map((bucket) => {
    const presentation = expenseConceptPresentation(bucket.concept);
    return {
      key: bucket.concept,
      label: presentation.label,
      secondaryLabel: presentation.secondaryLabel,
      total: bucket.gross_minor,
      segments: [
        {key: "deductible", label: t("charts.expenses.deductible"), tone: "accent", pattern: "solid", value: bucket.deductible_minor},
        {key: "non-deductible", label: t("charts.expenses.nonDeductible"), tone: "warning", pattern: "hatched", value: bucket.non_deductible_minor},
      ],
    };
  });
  return spec;
}

function buildReviewAgingSpec(analytics: Analytics) {
  const aging = analytics.datasets.review_aging;
  const spec = chartSpecBase(
    "review-aging",
    "charts.aging.title",
    "charts.aging.aria",
    t("charts.aging.empty")
  );
  spec.bucketLabel = t("charts.aging.bucketLabel");
  spec.formatValue = (value) => String(value);
  const counts = aging.counts || {};
  const total = reviewQueueTotal(analytics);
  if (!total) {
    spec.rows = [];
    return spec;
  }
  const seriesOrder = [
    {key: "received", label: statusLabel("received"), tone: "info"},
    {key: "extracted", label: statusLabel("extracted"), tone: "warning"},
    {key: "needs_review", label: statusLabel("needs_review"), tone: "accent"},
    {key: "approved_unposted", label: t("charts.aging.approvedOverdue"), tone: "danger"},
  ];
  spec.rows = (aging.buckets || []).map((bucket, index) => ({
    key: bucket,
    label: bucket,
    segments: seriesOrder.map((series) => ({
      key: series.key,
      label: series.label,
      tone: series.tone,
      pattern: "solid",
      value: (counts[series.key] || [])[index] ?? 0,
    })),
  }));
  return spec;
}

function buildCounterpartySpec(analytics: Analytics) {
  const concentration = analytics.datasets.counterparty_concentration;
  const spec = chartSpecBase(
    "counterparty-concentration",
    "charts.counterparties.title",
    "charts.counterparties.aria",
    t("charts.counterparties.empty")
  );
  spec.bucketLabel = "";
  const rows = (concentration.top || []).map((entry) => ({
    key: String(entry.counterparty_id || "none"),
    label: entry.name || t("charts.counterparties.noname"),
    segments: [
      {key: "income", label: t("charts.counterparties.income"), tone: "info", pattern: "solid", value: entry.income_minor},
    ],
  }));
  if (concentration.other_minor) {
    rows.push({
      key: "other",
      label: t("charts.counterparties.other"),
      segments: [
        {key: "income", label: t("charts.counterparties.income"), tone: "info", pattern: "hatched", value: concentration.other_minor},
      ],
    });
  }
  spec.rows = rows;
  return spec;
}

function buildAmortizationSpec(analytics: Analytics) {
  const amortization = analytics.datasets.amortization;
  const spec = chartSpecBase(
    "amortization",
    "charts.amortization.title",
    "charts.amortization.aria",
    t("charts.amortization.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.note = t("charts.amortization.note");
  const points = amortization.points || [];
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "amortization", label: t("charts.amortization.perQuarter"), kind: "bar", stack: "amortization", tone: "accent", pattern: "solid", values: points.map((point) => point.total_minor)},
  ];
  return spec;
}

return {businessResult: buildBusinessResultSpec, taxDue: buildTaxDueSpec, ivaPosition: buildIvaPositionSpec,
    reserve: buildReserveSpec, cumulativeNet: buildCumulativeNetSpec, yearComparison: buildYearComparisonSpec,
    expenseStructure: buildExpenseStructureSpec, reviewAging: buildReviewAgingSpec, counterparty: buildCounterpartySpec,
    amortization: buildAmortizationSpec};
}
export type ChartKind = keyof ReturnType<typeof createChartBuilders>;
