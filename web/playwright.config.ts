import { defineConfig, devices } from '@playwright/test';

// End-to-end tests run against the real service serving the built app (docs/plan/05-architecture.md, 5.11).
const PORT = Number(process.env.SKY_E2E_PORT ?? 8781);
const UV = process.env.UV ?? 'uv';

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } }],
  webServer: {
    command: `${UV} run --project .. uvicorn service.app:app --host 127.0.0.1 --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    env: { SKY_WEB_DIST: 'web/dist', SKY_STORE: process.env.SKY_STORE ?? '/tmp/sky-e2e-store' },
  },
});
