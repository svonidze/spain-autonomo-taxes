import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {baseURL: 'http://127.0.0.1:8765', headless: true, trace: 'retain-on-failure'},
  webServer: {
    command: 'python tests/browser/serve_fixture.py',
    url: 'http://127.0.0.1:8765',
    reuseExistingServer: false,
    env: {PYTHONPATH: 'src'},
    timeout: 30_000,
  },
});
