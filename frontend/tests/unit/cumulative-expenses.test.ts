import { expect, test } from 'vitest';
import { createChartBuilders } from '../../src/charts/builders.ts';
import { renderCartesian } from '../../src/charts/renderer.js';
import type { Analytics } from '../../src/charts/types.ts';
import fixture from '../fixtures/analytics.json' with { type: 'json' };

for (const locale of ['ru', 'en'] as const)
  for (const values of [
    [0, 0],
    [10000, 12000],
    [null, null],
  ])
    test(`missing FX remains visible with zero, mixed or empty data in ${locale}: ${values}`, () => {
      const data = structuredClone(fixture) as unknown as Analytics;
      data.datasets.cumulative_expenses = {
        buckets: ['2026-01', '2026-02'],
        gross_actual_minor: values,
        gross_projected_minor: values,
        deductible_actual_minor: values,
        deductible_projected_minor: values,
        missing_fx_transaction_count: 2,
      };
      const spec = createChartBuilders(locale).cumulativeExpenses(data);
      const root = document.createElement('div');
      renderCartesian(root, spec);
      expect(root.querySelector('.chart-note')!.textContent).toContain(
        locale === 'en'
          ? 'Expense rows without confirmed EUR conversion are excluded: 2.'
          : 'В расчет не вошли расходы без подтвержденного пересчета в EUR: 2.',
      );
      if (values[0] != null) {
        expect(root.querySelectorAll('.chart-legend li')).toHaveLength(4);
        expect(root.querySelector('table')!.textContent).toContain(
          locale === 'en' ? 'IRPF deductions: actual' : 'Вычеты IRPF: факт',
        );
      }
    });
