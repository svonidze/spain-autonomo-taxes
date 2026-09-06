import {test, expect} from '@playwright/test';
import messages from '../../frontend/src/locales/en/workflow.json' with {type: 'json'};
const ASSET = '55555555-5555-4555-8555-555555555555', ENTRY = '66666666-6666-4666-8666-666666666666';
const asset = {asset_id: ASSET, asset_code: 'SYN-ASSET', description: 'Synthetic equipment', cost_minor: 12100, amortizable_base_minor: 10000, business_use_percent: 100, annual_rate_percent: 25, ui_context: {domain: 'asset', state: 'ready', reasons: []}};
const schedule = {native: true, rows: [{amortization_entry_id: ENTRY, period_key: '2026-Q3', amount_minor: 2500, can_post: true, row_version: 3}]};
test('depreciation retry retains request ID without storage and does not repost after refresh failure', async ({page}) => {
  await page.addInitScript(() => {Object.defineProperty(window, 'sessionStorage', {get() {throw new Error('Disabled synthetic storage');}}); localStorage.setItem('autonomo.locale','en');});
  let writes = 0, reads = 0; const bodies: {request_id: string; expected_version: number}[] = [];
  await page.route('**/api/assets?**', route => route.fulfill({json: [asset]}));
  await page.route(`**/api/assets/${ASSET}/schedule`, route => ++reads === 1 ? route.fulfill({json: schedule}) : route.fulfill({status: 503, json: {error: 'Synthetic schedule unavailable'}}));
  await page.route(`**/api/depreciation/${ENTRY}/post`, route => {bodies.push(route.request().postDataJSON()); return ++writes === 1 ? route.fulfill({status: 503, json: {error: 'Synthetic transient failure'}}) : route.fulfill({json: {follow_up_pending: false}});});
  await page.goto('/assets?period=2026-Q3'); await page.locator('#asset-plan-select').selectOption(ASSET);
  const post = page.locator('[data-recognize]'); await post.click();
  await expect(page.locator('#wf-schedule-status')).toHaveText('Synthetic transient failure'); await expect(post).toBeEnabled();
  await post.click();
  await expect(page.locator('#wf-schedule-status')).toHaveText(messages['workflow.depreciationPostedReloadThePageToRetrieveCurrentData']);
  await expect(post).toBeDisabled(); expect(writes).toBe(2); expect(bodies[0]).toEqual(bodies[1]); expect(bodies[0]!.expected_version).toBe(3);
});
test('pending depreciation survives locale changes and late completion does not replace another screen', async ({page}) => {
  let release!: () => void, writes = 0;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/assets?**', route => route.fulfill({json: [asset]}));
  await page.route(`**/api/assets/${ASSET}/schedule`, route => route.fulfill({json: schedule}));
  await page.route(`**/api/depreciation/${ENTRY}/post`, async route => {writes++; await gate; await route.fulfill({json: {follow_up_pending: false}});});
  await page.goto('/assets?period=2026-Q3'); await page.locator('#asset-plan-select').selectOption(ASSET); await page.locator('[data-recognize]').click();
  await page.locator('[data-locale="en"]').click(); await expect(page.locator('[data-recognize]')).toBeDisabled(); await expect(page.locator('#asset-plan-select')).toHaveValue(ASSET);
  await page.locator('a[data-view="income"]').click(); release();
  await expect(page.locator('#transaction-search')).toBeVisible(); await expect(page.locator('#asset-plan-detail')).toHaveCount(0); expect(writes).toBe(1);
});
test('tax payable zero and carry-forward retain distinct meanings through locale changes', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('autonomo.locale','en'));
  await page.route('**/api/taxes?**', route => route.fulfill({json: {period:'2026-Q3',period_state:{phase:'current'},obligations:[{obligation_code:'303',determination:'due',filing_status:'filed'}],tax_forms:{modelo303:{form_code:'303',display_state:'filed',values:{'71':0,compensation_carryforward:50},headline_value:0}},tax_summary:{settlement_status:'no_payment_required',total_payable_minor:0,total_confirmed_paid_minor:0,outstanding_minor:0,forms:{'303':{settlement_status:'no_payment_required',determination:'due',filing_status:'filed',payable_minor:0,disposition:'carryforward',carryforward_minor:5000}}}}}));
  await page.goto('/taxes?period=2026-Q3');
  const iva = page.locator('.tax-form-card').filter({has: page.getByRole('heading',{name:'Modelo 303'})});
  await expect(iva.locator('.tax-form-amount > strong')).toHaveText('€0.00'); await expect(iva).toContainText('€50.00');
  await iva.locator('summary').click(); await expect(iva.locator('.casilla-grid')).toContainText('€0.00');
  await page.locator('[data-locale="ru"]').click(); await expect(iva.locator('details')).toHaveAttribute('open','');
  await expect(iva.locator('.tax-form-amount > strong')).toContainText('0,00');
});
test('undecided returns stay distinct from filing exemptions in both locales', async ({page}) => {
  await page.addInitScript(() => localStorage.setItem('autonomo.locale', 'en'));
  await page.route('**/api/taxes?**', route => route.fulfill({json: {
    period: '2026-Q3', period_state: {phase: 'current'},
    obligations: [
      {obligation_code: '130', determination: 'unknown'},
      {obligation_code: '303', determination: 'not_due'},
      {obligation_code: '349', determination: 'unknown'},
      {obligation_code: '390', determination: 'not_due'},
    ],
    tax_forms: {},
    tax_summary: {settlement_status: 'undetermined', forms: {
      '130': {determination: 'unknown', settlement_status: 'undetermined'},
      '303': {determination: 'not_due', settlement_status: 'not_due'},
    }},
  }}));
  await page.goto('/taxes?period=2026-Q3');
  const irpf = page.locator('.tax-form-card').filter({has: page.getByRole('heading', {name: 'Modelo 130'})});
  const iva = page.locator('.tax-form-card').filter({has: page.getByRole('heading', {name: 'Modelo 303'})});
  const undecided = page.locator('.tax-other-forms').filter({hasText: 'Modelo 349'});
  const exempt = page.locator('.tax-other-forms').filter({hasText: 'Modelo 390'});
  await expect(irpf.locator('.tax-form-facts')).toContainText('Filing decision pending');
  await expect(iva.locator('.tax-form-facts')).toContainText('Filing not required');
  await expect(undecided.locator('summary')).toHaveText('Returns without a decision — review the obligations');
  await expect(undecided).toHaveAttribute('open', '');
  await expect(exempt).not.toContainText('Modelo 349');
  await page.locator('[data-locale="ru"]').click();
  await expect(irpf.locator('.tax-form-facts')).toContainText('Решение по подаче не принято');
  await expect(iva.locator('.tax-form-facts')).toContainText('Подача не требуется');
  await expect(undecided.locator('summary')).toHaveText('Декларации без решения — проверьте обязательства');
});
test('Vue expanded chart is CSP-safe, uses cached locale data and closes on route disposal', async ({page}) => {
  let reads = 0;
  await page.addInitScript(() => {const observed: string[] = []; (window as unknown as {csp: string[]}).csp = observed; document.addEventListener('securitypolicyviolation', event => observed.push(event.violatedDirective));});
  await page.route('**/api/analytics?**', async route => {reads++; await route.continue();});
  await page.goto('/dashboard?period=2026-Q3');
  await page.locator('#chart-business-result .chart-expand-button').click();
  await expect(page.locator('#vue-chart-businessResult')).toBeVisible(); const before = reads;
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-chart-businessResult h2')).toHaveText('Income and deductible expenses by month'); expect(reads).toBe(before);
  await page.keyboard.press('Escape'); await expect(page.locator('#chart-business-result .chart-expand-button')).toBeFocused();
  await page.locator('a[data-view="income"]').click(); await expect(page.locator('#vue-chart-businessResult')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as {csp: string[]}).csp)).toEqual([]);
});
