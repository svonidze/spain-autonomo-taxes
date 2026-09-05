// Transitional VM contexts consume the real typed modules, never copied helpers.
const {legacyCore: core} = require('../frontend/src/core/legacy.ts');
globalThis.AutonomoCore = core;
function prepare(context = {}) {
  context.AutonomoCore = core;
  return context;
}
module.exports = {core, prepare};
