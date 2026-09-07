import { createApp } from 'vue';
import Shell from './shell/Shell.vue';
import { router } from './shell/router.ts';
import './styles/styles.css';
createApp(Shell).use(router).mount(document.body);
