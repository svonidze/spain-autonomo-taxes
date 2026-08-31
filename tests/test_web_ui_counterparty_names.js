const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../src/autonomo_taxes/web_ui/app.js"), "utf8");
const context = vm.createContext({URL, console, window: {location: {origin: "http://localhost"}, confirm: () => true}});
vm.runInContext(source, context); // No DOM at boot: exercise the real functions without starting the app.
vm.runInContext(`
  this.names = {
    valid: validCounterpartyName, cell: counterpartyNameCell,
    open: openCounterpartyNameEditor, close: closeCounterpartyNameEditor,
    submit: submitCounterpartyName, accept: acceptCurrentCounterpartyName,
    history: loadCounterpartyNameHistory, transport: fetchJSON,
    put: row => counterpartyRowsById.set(row.counterparty_id, row),
    editor: () => counterpartyNameEditor,
  };
`, context);

const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    value: "", textContent: "", innerHTML: "", hidden: false, disabled: false, open: false,
    setAttribute(key, value) { this[key] = value; },
    removeAttribute(key) { delete this[key]; },
    focus() { this.focused = true; },
    showModal() { this.open = true; },
    close() { this.open = false; },
  });
  return elements.get(selector);
}
context.document = {querySelector: element};
const notices = [];
context.showToast = (message) => notices.push(message);
const names = context.names;
const row = {counterparty_id: "3906f899-02d0-41bb-8ddc-01577a200001", display_name: "Synthetic Supplier,", row_version: 1};
const submit = () => names.submit({preventDefault() {}});
const input = () => element("#counterparty-name-input");
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

async function main() {
  for (const value of ["", "  ", "A\nB", "A\tB", "A\u2028B", "A\x7fB", null, 23]) {
    assert.equal(names.valid(value), false);
  }
  assert.equal(names.valid('  Synthetic «Name», <script>  '), true);
  assert.match(names.cell({...row, display_name: '<script>test</script>'}), /&lt;script&gt;/);
  assert.doesNotMatch(names.cell({...row, display_name: '<script>test</script>'}), /<script>/);

  context.fetch = async () => ({
    ok: false, status: 409, headers: {get: () => "application/json"},
    text: async () => JSON.stringify({error: "stale", code: "stale_counterparty", current: row}),
  });
  await assert.rejects(names.transport("/api/test"), error =>
    error.message === "stale" && error.status === 409 && error.code === "stale_counterparty" &&
    error.current.counterparty_id === row.counterparty_id
  );

  context.fetchJSON = async () => ({changes: []});
  names.put(row);
  names.open(row.counterparty_id, {isConnected: true, focus() {}});
  await settle();
  input().value = "   ";
  await submit();
  assert.equal(input()["aria-invalid"], "true");
  assert.equal(names.editor().busy, false);

  input().value = "Synthetic Supplier";
  let finish;
  let writes = 0;
  context.fetchJSON = async (url, options) => {
    writes += 1;
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), {display_name: "Synthetic Supplier", expected_row_version: 1});
    return new Promise(resolve => { finish = resolve; });
  };
  const pending = submit();
  await submit();
  assert.equal(writes, 1);
  assert.equal(element("#save-counterparty-name").disabled, true);
  finish({...row, display_name: "Synthetic Supplier", row_version: 2, changed: true});
  await pending;
  assert.equal(names.editor(), null);
  assert.equal(element("#counterparty-name-dialog").open, false);
  assert.equal(notices.length, 1);

  context.fetchJSON = async () => ({changes: []});
  names.open(row.counterparty_id, null);
  await settle();
  input().value = "Synthetic Draft";
  context.fetchJSON = async () => {
    throw Object.assign(new Error("stale"), {status: 409, code: "stale_counterparty",
      current: {...row, display_name: "Synthetic Remote", row_version: 3}});
  };
  await submit();
  assert.equal(input().value, "Synthetic Draft");
  assert.equal(element("#save-counterparty-name").disabled, true);
  assert.equal(element("#counterparty-name-conflict").hidden, false);
  assert.match(element("#counterparty-current-name").textContent, /Synthetic Remote/);
  context.fetchJSON = async () => ({changes: []});
  names.accept();
  await settle();
  assert.equal(names.editor().row.row_version, 3);
  assert.equal(input().value, "Synthetic Draft");
  assert.equal(element("#save-counterparty-name").disabled, false);
  context.window.confirm = () => false;
  names.close();
  assert.notEqual(names.editor(), null);
  context.window.confirm = () => true;
  names.close();
  assert.equal(names.editor(), null);

  context.fetchJSON = async () => { throw new Error("offline"); };
  names.open(row.counterparty_id, null);
  await settle();
  assert.equal(element("#retry-counterparty-name-history").hidden, false);
  assert.match(element("#counterparty-name-history-body").textContent, /Не удалось/);
  input().value = "Synthetic offline draft";
  await submit();
  assert.equal(input().value, "Synthetic offline draft");
  assert.equal(element("#save-counterparty-name").disabled, false);
  assert.equal(element("#counterparty-name-error").textContent, "offline");
  names.close(true);
  console.log("Counterparty editor behavior passed");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
