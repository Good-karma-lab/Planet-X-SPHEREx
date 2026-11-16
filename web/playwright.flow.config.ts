import { defineConfig, devices } from '@playwright/test';

// The product flow, end to end, against a build that is already running (the integration build of branch product-flow:
// API 8790, web 5190, SKY_AUTH_DEV=1). Nothing is started here.   npx playwright test -c playwright.flow.config.ts
export default defineConfig({
  testDir: 'tests/e2e',
  testMatch: '**/flow.e2e.ts',
  timeout: 240_000,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.SKY_FLOW_URL ?? 'http://localhost:5190',
    ...devices['Desktop Chrome'],
    viewport: { width: 1512, height: 860 },
    deviceScaleFactor: 2,
    screenshot: 'only-on-failure',
  },
});
