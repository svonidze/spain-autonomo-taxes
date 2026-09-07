import { createApp, nextTick } from 'vue';
import { expect, test } from 'vitest';
import DecisionField from '../../src/features/review/DecisionField.vue';
import { setLocale } from '../../src/core/i18n.ts';
test('minor-unit field preview preserves zero and clears blank or nonnumeric input', async () => {
  setLocale('en');
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(DecisionField, {
    field: {
      path: 'deduction',
      label: 'fields.total',
      minor: true,
      type: 'number',
      valueType: 'integer',
    },
    value: '',
  });
  app.mount(root);
  const input = root.querySelector('input')!,
    output = root.querySelector('output')!;
  for (const value of ['0', '12345', '', 'invalid']) {
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await nextTick();
    if (value === '0') expect(output.textContent).toContain('€0.00');
    else if (value === '12345') expect(output.textContent).toContain('€123.45');
    else expect(output.textContent).toBe('');
  }
  app.unmount();
  root.remove();
  setLocale('ru');
});
