import { expect, test } from 'vitest';
import { createChartBuilders, type ChartKind } from '../../src/charts/builders.ts';
import type { Analytics } from '../../src/charts/types.ts';
import fixture from '../fixtures/analytics.json' with { type: 'json' };
import expected from '../fixtures/chart-expected.json' with { type: 'json' };
for (const locale of ['ru', 'en'] as const)
  test(`chart specs preserve the reviewed baseline in ${locale}`, () => {
    const builders = createChartBuilders(locale);
    for (const kind of Object.keys(expected[locale]) as ChartKind[]) {
      const actual = builders[kind](fixture as unknown as Analytics),
        reference = expected[locale][kind];
      expect(JSON.parse(JSON.stringify(actual)), kind).toEqual(reference.spec);
      expect(actual.formatValue(-123456), kind).toBe(reference.value);
      expect(actual.formatPercent?.(0.314) ?? null, kind).toBe(reference.percent);
    }
  });
