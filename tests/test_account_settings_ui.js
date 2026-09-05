const __uiCore = require('./legacy_core.cjs');
const assert = require('node:assert/strict');
const UI = require('../src/autonomo_taxes/web_ui/settings.js');
const decode = text => text.replaceAll('&quot;', '"').replaceAll('&#39;', "'").replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');
class Element {
  constructor() { this.listeners = {}; this.value = ''; this.textContent = ''; this.disabled = false; this.classList = {toggle() {}}; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  setAttribute() {}
}
class Form extends Element {
  constructor(html) {
    super(); this.feedback = new Element(); this.fields = new Map();
    this.fieldset = new Element();
    Object.defineProperty(this.fieldset, 'innerHTML', {set: value => this.parse(value)});
    this.elements = {namedItem: name => this.fields.get(name)};
    this.parse(html);
  }
  parse(html) { for (const match of html.matchAll(/<input\b([^>]+)>/g)) {
    const attrs = match[1], name = /name="([^"]+)"/.exec(attrs)?.[1];
    if (name) { const element = new Element(); element.value = decode(/value="([^"]*)"/.exec(attrs)?.[1] || ''); this.fields.set(name, element); }
  } }
  querySelector(selector) { return selector === 'fieldset' ? this.fieldset : this.feedback; }
}
class Container {
  constructor() { this.nodes = new Map(); }
  set innerHTML(value) {
    this.html = value;
    for (const match of value.matchAll(/<form id="([^"]+)">([\s\S]*?)<\/form>/g)) this.nodes.set('#' + match[1], new Form(match[2]));
  }
  querySelector(selector) {
    if (selector === '#settings-backup-form' && !this.nodes.has(selector)) return null;
    if (!this.nodes.has(selector)) { const element = new Element(); element.querySelector = () => null; this.nodes.set(selector, element); }
    return this.nodes.get(selector);
  }
}
const profile = {taxpayer_profile_id: 'synthetic-profile', row_version: 1, full_name: 'Example Taxpayer', tax_id: 'TEST-TAX-ID-001', residency_country: 'ES', activities: []};
const UPDATED_NAME = profile.full_name + ' Updated';
const HTML_NAME = '<img src=x onerror=bad()>';
function setup({request, confirm = () => true, profiles = [profile], current = () => true} = {}) {
  const container = new Container(), requests = [], names = [];
  const controller = UI.mount(container, {data: {profiles: structuredClone(profiles), backups: {available: true, revision: 'missing', daily_keep: null, monthly_keep: null, last_success: {daily: null, monthly: null}}},
    locale: 'en', request: async (url, options) => { requests.push({url, ...options}); return request ? request(url, JSON.parse(options.body)) : {...profile, full_name: UPDATED_NAME, row_version: 2}; },
    isCurrent: current, onProfile: name => names.push(name), onLocale() {}, onReload() {}, confirm});
  const form = container.querySelector('#settings-profile-form'), backup = container.querySelector('#settings-backup-form');
  return {container, controller, form, backup, requests, names};
}
const submit = form => form.listeners.submit({preventDefault() {}});
(async () => {
  assert.deepEqual(Object.keys(UI.copy.ru).sort(), Object.keys(UI.copy.en).sort());
  assert.match(UI.profileFields({...profile, full_name: HTML_NAME}, UI.translator('en')), /&lt;img/);
  assert.doesNotMatch(UI.profileFields({...profile, full_name: HTML_NAME}, UI.translator('en')), /value="<img/);
  assert.match(UI.pruningText({daily_keep: null, monthly_keep: 3}, 'en'), /value and remaining copy count are unknown/);
  assert.match(UI.pruningText({daily_keep: 7, monthly_keep: 3}, 'en'), /retain at most 7/);
  assert.match(UI.statusHtml({last_success: {daily: {recorded_at: '2026-09-04T00:00:00Z', keep: 7, offsite: true, offsite_status: 'acknowledged'}, monthly: null}}, 'en'), /recovery is verified separately/);
  assert.match(UI.verificationHtml({recovery_verification: {monthly: {last_attempt: {recorded_at: '2026-09-04T00:00:00Z', status: 'failed'}, last_success: {recorded_at: '2026-08-02T00:00:00Z', status: 'success'}}}}, 'en'), /Previous successful verification/);
  {
    const h = setup();
    h.form.elements.namedItem('full_name').value = UPDATED_NAME;
    h.backup.elements.namedItem('daily_keep').value = '14';
    assert.equal(h.controller.isDirty(), true);
    await submit(h.form);
    assert.equal(JSON.parse(h.requests[0].body).expected_row_version, 1);
    assert.deepEqual(h.names, [UPDATED_NAME]);
    assert.equal(h.backup.elements.namedItem('daily_keep').value, '14');
    assert.equal(h.controller.isDirty(), true); // Saving profile cannot discard backup draft.
    assert.equal(h.form.fieldset.disabled, false);
  }
  {
    const h = setup({request: async () => { throw Object.assign(new Error('conflict'), {status: 409}); }, confirm: () => false});
    h.form.elements.namedItem('full_name').value = 'Keep this draft';
    await submit(h.form);
    assert.equal(h.form.elements.namedItem('full_name').value, 'Keep this draft');
    assert.match(h.form.feedback.textContent, /input remains/);
    assert.equal(h.controller.canLeave(), false);
  }
  {
    const h = setup({confirm: () => false});
    h.backup.elements.namedItem('daily_keep').value = '14';
    await submit(h.backup);
    assert.equal(h.requests.length, 0);
  }
  {
    const h = setup({request: async (_url, payload) => ({available: true, revision: 'new', daily_keep: payload.daily_keep, monthly_keep: payload.monthly_keep})});
    h.backup.elements.namedItem('daily_keep').value = '14';
    await submit(h.backup);
    const sent = JSON.parse(h.requests[0].body);
    assert.equal(sent.confirm_local_pruning, true);
    assert.equal(sent.monthly_keep, null);
    assert.equal(h.controller.isDirty(), false);
    assert.match(h.backup.feedback.textContent, /not yet confirmed/);
  }
  {
    let resolve;
    const h = setup({request: () => new Promise(done => { resolve = done; })});
    h.form.elements.namedItem('full_name').value = 'Pending';
    const pending = submit(h.form);
    assert.equal(h.controller.isBusy(), true);
    assert.equal(h.controller.canLeave(), false);
    await submit(h.form);
    assert.equal(h.requests.length, 1);
    h.controller.dispose();
    resolve({...profile, row_version: 2}); await pending;
    assert.deepEqual(h.names, []);
  }
  console.log('Settings forms, confirmation, drafts, conflicts and late responses passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
