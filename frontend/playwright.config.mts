import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  testDir: resolve(frontendRoot, 'tests/e2e'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:8765', headless: true, trace: 'retain-on-failure' },
  webServer: {
    command: 'node scripts/serve_browser.cjs',
    cwd: frontendRoot,
    url: 'http://127.0.0.1:8765',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
