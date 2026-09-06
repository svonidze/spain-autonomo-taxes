export type ChartValue = number | null | undefined;
export interface ChartSeries {
  key: string;
  label: string;
  kind?: string;
  stack?: string;
  tone?: string;
  pattern?: string;
  values: ChartValue[];
}
export interface ChartSegment {
  key: string;
  label: string;
  tone?: string;
  pattern?: string;
  value: ChartValue;
}
export interface ChartRow {
  key: string;
  label: string;
  secondaryLabel?: string;
  total?: ChartValue;
  segments: ChartSegment[];
}
export interface ChartSpec {
  chartId: string;
  title: string;
  ariaLabel?: string;
  formatValue(value: number): string;
  width?: number;
  bucketLabel?: string;
  buckets?: string[];
  series?: ChartSeries[];
  rows?: ChartRow[];
  ranges?: ChartSegment[];
  measure?: ChartSegment | null;
  emptyMessage?: string;
  unavailableMessage?: string;
  note?: string;
  tableLabel?: string;
  tableInitiallyOpen?: boolean;
  tablePrimaryOnNarrow?: boolean;
  totalLabel?: string;
  totalValueLabel?: string;
  shareUnavailableLabel?: string;
  formatPercent?(ratio: number): string;
  showSegmentShares?: boolean;
  rowLabelWidth?: number;
  rowHeight?: number;
  expandAction?: { label: string; handler(trigger: HTMLElement): void };
}
export interface ChartMark {
  kind: string;
  seriesKey?: string;
  className?: string;
  hatchTone?: string | null;
  title?: string;
  x?: number;
  y?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
  width?: number;
  height?: number;
  points?: string;
  d?: string;
  cx?: number;
  cy?: number;
  r?: number;
}
export interface ChartLabel {
  x: number;
  y: number;
  label: string;
  className?: string;
}
export interface ChartScene {
  chartId: string;
  kind: string;
  rows?: ChartRow[];
  ariaLabel?: string;
  empty: boolean;
  viewBox: { width: number; height: number };
  marks: ChartMark[];
  legend: { key: string; label: string; tone: string; pattern: string }[];
  hatchTones: string[];
  series?: ChartSeries[];
  rowLabels?: ChartLabel[];
  valueLabels?: ChartLabel[];
  bucketLabels?: ChartLabel[];
  grid?: { x1: number; x2: number; y: number; label: string }[];
  baselineY?: number;
  baselineX1?: number;
  baselineX2?: number;
  measureUnavailable?: boolean;
}
export type ChartRenderer = (container: HTMLElement, spec: ChartSpec) => HTMLElement;
export interface TaxPoint {
  period_key: string;
  source?: string;
  payable_minor?: ChartValue;
}
interface TaxSeriesPoint extends TaxPoint {
  modelo130: TaxPoint;
  modelo303: TaxPoint;
}
interface BusinessScope {
  income_base_minor: ChartValue[];
  deductible_expense_minor: ChartValue[];
}
interface YearSummary {
  year: number;
  taxable_income_minor: ChartValue;
  deductible_expense_minor: ChartValue;
  net_minor: ChartValue;
}
export interface Analytics {
  quality?: { missing_fx_transaction_count: number };
  datasets: {
    business_result: {
      monthly: {
        buckets: string[];
        actual: BusinessScope;
        approved_unposted: BusinessScope;
        approved_future: BusinessScope;
      };
    };
    quarterly_tax_due: { points: TaxSeriesPoint[] };
    iva_position: {
      points: (TaxPoint & {
        output_vat_minor: ChartValue;
        deductible_input_vat_minor: ChartValue;
        result_minor: ChartValue;
      })[];
    };
    reserve_bullet: {
      status: string;
      required_tax_minor: ChartValue;
      recommended_reserve_minor: ChartValue;
      available_minor: ChartValue;
    };
    cumulative_net: {
      buckets: string[];
      projected_minor: ChartValue[];
      actual_minor: ChartValue[];
    };
    ytd_comparison: { previous_year: YearSummary; current_year: YearSummary };
    expense_structure: {
      buckets: {
        concept: string;
        gross_minor: ChartValue;
        deductible_minor: ChartValue;
        non_deductible_minor: ChartValue;
      }[];
    };
    review_aging: { buckets: string[]; counts: Record<string, number[]> };
    counterparty_concentration: {
      top: { counterparty_id: string | null; name: string; income_minor: ChartValue }[];
      other_minor: ChartValue;
    };
    amortization: { points: { period_key: string; total_minor: ChartValue }[] };
  };
}
