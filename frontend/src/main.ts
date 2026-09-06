import {createApp} from 'vue';
import './core/install.ts';
// Remaining read-only adapters are removed at finalization after oracle retirement.
import '../../src/autonomo_taxes/web_ui/status-help.js';
import './charts/legacy.ts';
import '../../src/autonomo_taxes/web_ui/expense-workflow.js';
import '../../src/autonomo_taxes/web_ui/settings.js';
import Shell from './shell/Shell.vue';
import {router} from './shell/router.ts';
import '../../src/autonomo_taxes/web_ui/styles.css';
createApp(Shell).use(router).mount(document.body);
