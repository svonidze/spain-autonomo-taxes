import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { existsSync, lstatSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { localizeShell } from './scripts/shell_locales.mts';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const repositoryRoot = resolve(frontendRoot, '..');
const packageRoot = resolve(repositoryRoot, 'backend/src/autonomo_taxes');
const webUiRoot = resolve(packageRoot, 'web_ui');
const generatedUiRoot = resolve(webUiRoot, 'dist');

function clearGeneratedUiRoot() {
  const canonicalRepositoryRoot = realpathSync(repositoryRoot);
  const canonicalPackageRoot = realpathSync(packageRoot);
  if (canonicalPackageRoot !== resolve(canonicalRepositoryRoot, 'backend/src/autonomo_taxes')) {
    throw new Error(
      `Refusing UI output outside the expected source package: ${canonicalPackageRoot}`,
    );
  }
  mkdirSync(webUiRoot, { recursive: true });
  if (lstatSync(webUiRoot).isSymbolicLink()) {
    throw new Error(`Refusing symlinked UI output parent: ${webUiRoot}`);
  }
  const canonicalWebUiRoot = realpathSync(webUiRoot);
  if (relative(canonicalPackageRoot, canonicalWebUiRoot) !== 'web_ui') {
    throw new Error(`Refusing unexpected UI output parent: ${canonicalWebUiRoot}`);
  }
  if (existsSync(generatedUiRoot)) {
    const outputStatus = lstatSync(generatedUiRoot);
    if (outputStatus.isSymbolicLink() || !outputStatus.isDirectory()) {
      throw new Error(`Refusing unexpected UI output: ${generatedUiRoot}`);
    }
    if (relative(canonicalWebUiRoot, realpathSync(generatedUiRoot)) !== 'dist') {
      throw new Error(`Refusing UI output outside its package: ${generatedUiRoot}`);
    }
  }
  rmSync(generatedUiRoot, { recursive: true, force: true });
  mkdirSync(generatedUiRoot, { recursive: true });
}

export default defineConfig({
  plugins: [
    vue(),
    { name: 'catalog-shell', transformIndexHtml: { order: 'pre', handler: localizeShell } },
    { name: 'clear-generated-ui-output', buildStart: clearGeneratedUiRoot },
  ],
  root: frontendRoot,
  publicDir: false,
  envDir: false,
  envPrefix: [],
  build: {
    outDir: generatedUiRoot,
    assetsDir: 'ui-assets',
    emptyOutDir: false,
    target: 'es2022',
    sourcemap: false,
  },
});
