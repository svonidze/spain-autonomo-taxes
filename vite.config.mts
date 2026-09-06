import {defineConfig} from 'vite';
import vue from '@vitejs/plugin-vue';
import {fileURLToPath} from 'node:url';
import {localizeShell} from './scripts/shell_locales.mts';

export default defineConfig({
  plugins: [vue(), {name: 'catalog-shell', transformIndexHtml: {order: 'pre', handler: localizeShell}}],
  root: fileURLToPath(new URL('./src/autonomo_taxes/web_ui', import.meta.url)),
  publicDir: false,
  envDir: false,
  envPrefix: [],
  build: {
    outDir: fileURLToPath(new URL('./packages/ui/src/autonomo_taxes_ui/dist', import.meta.url)),
    assetsDir: 'ui-assets',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
});
