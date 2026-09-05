import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('./src/autonomo_taxes/web_ui', import.meta.url)),
  publicDir: false,
  envDir: false,
  envPrefix: [],
  build: {
    outDir: 'dist',
    assetsDir: 'ui-assets',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
  },
});
