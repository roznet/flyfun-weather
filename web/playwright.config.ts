import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '*.spec.ts',
  // Schedule tests, not files: a spec file's tests are independent (each gets
  // its own page and route mocks), and one long file on one worker otherwise
  // sets the wall time while the other workers sit idle.
  fullyParallel: true,
  // Locally the default (half the cores) is past the knee; on CI's 4 vCPUs it
  // would be 2.
  workers: process.env.CI ? 4 : undefined,
  timeout: 30_000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:8000',
    headless: true,
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  webServer: {
    // Call the venv's uvicorn directly rather than activating first: the
    // command runs under /bin/sh, which is dash on Ubuntu, where `source` is
    // not a builtin (`source: not found`, exit 127). Invoking the entrypoint
    // needs no activation and behaves the same on macOS and CI.
    command: 'cd .. && venv/bin/uvicorn weatherbrief.api.app:app --port 8000',
    url: 'http://localhost:8000',
    reuseExistingServer: true,
  },
});
