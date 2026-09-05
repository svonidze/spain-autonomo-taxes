import {legacyCore} from './legacy.ts';

declare global { var AutonomoCore: typeof legacyCore; }
globalThis.AutonomoCore = legacyCore;
legacyCore.setLocale(legacyCore.loadLocale(() => localStorage));
legacyCore.installLocaleControls(document);
