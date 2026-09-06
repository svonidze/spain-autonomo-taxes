import { test, expect } from 'vitest';
import {
  answers,
  autoResolve,
  confirmFx,
  legacyErrorTarget,
  merge,
} from '../src/features/review/guided-helpers.ts';
import type { FxChoice, ReviewPacket } from '../src/features/review/guided-model.ts';
import reference from './fixtures/review-expected.json' with { type: 'json' };
test('guided answers, issue decisions, FX and diagnostic routing retain reviewed behavior', () => {
  const packet = structuredClone(reference.packet) as unknown as ReviewPacket;
  expect(answers(packet.decision, packet.state, reference.choice as FxChoice)).toEqual(
    reference.answers,
  );
  for (const row of reference.fx)
    expect(confirmFx(row.choice as FxChoice | null, row.suggestion)).toEqual(row.expected);
  autoResolve(packet, reference.coverage, answers(packet.decision, packet.state, null), 'ru');
  expect(packet.decision).toEqual(reference.resolved);
  for (const row of reference.errors) expect(legacyErrorTarget(row.error)).toBe(row.expected);
  expect(merge(reference.merge.base, reference.merge.override)).toEqual(reference.merge.expected);
});
