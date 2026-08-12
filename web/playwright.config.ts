import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '*.spec.ts',
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
