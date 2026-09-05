import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {expect, test} from 'vitest';
import {legacyCore} from '../src/core/legacy.ts';
import {createChartBuilders, type ChartKind} from '../src/charts/builders.ts';
import type {Analytics, ChartSpec} from '../src/charts/types.ts';
import fixture from './fixtures/analytics.json' with {type: 'json'};
const analytics = fixture as unknown as Analytics;
const names: Record<ChartKind, string> = {businessResult: 'buildBusinessResultSpec', taxDue: 'buildTaxDueSpec', ivaPosition: 'buildIvaPositionSpec', reserve: 'buildReserveSpec', cumulativeNet: 'buildCumulativeNetSpec', yearComparison: 'buildYearComparisonSpec', expenseStructure: 'buildExpenseStructureSpec', reviewAging: 'buildReviewAgingSpec', counterparty: 'buildCounterpartySpec', amortization: 'buildAmortizationSpec'};
for (const locale of ['ru','en'] as const) test(`typed chart domain mappings preserve the legacy oracle in ${locale}`, () => {
  const context = vm.createContext({AutonomoCore: legacyCore, Intl, Date, setTimeout, clearTimeout, URLSearchParams, console});
  vm.runInContext(readFileSync('src/autonomo_taxes/web_ui/app.js','utf8'), context);
  context.locale = locale; context.analytics = analytics;
  vm.runInContext('state.locale = locale', context);
  const builders = createChartBuilders(locale);
  for (const kind of Object.keys(names) as ChartKind[]) {
    const old = vm.runInContext(`${names[kind]}(analytics)`, context) as ChartSpec;
    const current = builders[kind](analytics);
    expect(JSON.parse(JSON.stringify(current)), kind).toEqual(JSON.parse(JSON.stringify(old)));
    expect(current.formatValue(-123456), kind).toBe(old.formatValue(-123456));
    if (current.formatPercent) expect(current.formatPercent(0.314), kind).toBe(old.formatPercent!(0.314));
  }
});
