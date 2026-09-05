import * as charts from './renderer.js';
// Legacy screens and old chart oracles are removed at finalization.
declare global {var AutonomoCharts: typeof charts;}
globalThis.AutonomoCharts = charts;
