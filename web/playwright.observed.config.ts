import { defineConfig } from '@playwright/test';

// Hermetic full-page checks: the test serves all assets/API fixtures through
// Playwright interception. No application server, shared DB or dist build.
export default defineConfig({
  testDir: './tests',
  testMatch: 'observed-browser.spec.ts',
  timeout: 30_000,
  workers: 1,
  retries: 0,
  use: {
    baseURL: 'http://observed.test',
    headless: true,
    viewport: { width: 1280, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    channel: process.env.OBSERVED_BROWSER_CHANNEL || undefined,
  },
});
