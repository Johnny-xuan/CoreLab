/**
 * Playwright config for CoreLab frontend E2E.
 *
 * Assumes the full docker compose stack is already up (caddy on :8080)
 * + the database has been wiped via `docker exec corelab-mysql ...`
 * so /setup/status returns false. The spec walks through the cold
 * start: setup wizard → login → dashboard.
 *
 * Browser install (`pnpm exec playwright install chromium`) and CI
 * wiring land alongside the GitHub Actions workflow (Phase 2 FU-1).
 */

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.e2e.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.CORELAB_E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
