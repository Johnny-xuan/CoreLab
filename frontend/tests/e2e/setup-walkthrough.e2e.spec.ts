/**
 * Phase 2 cold-start walkthrough.
 *
 * Preconditions (executed manually or by CI before this spec):
 *   docker exec corelab-mysql mysql -uroot -p"$ROOT_PWD" corelab \
 *     -e "DELETE FROM ssh_public_key; DELETE FROM audit_log; \
 *         DELETE FROM setup_token; DELETE FROM user; DELETE FROM lab;"
 *
 * The spec drives a real browser through:
 *   1. visiting / — router gate bounces to /setup
 *   2. completing the 3-step wizard
 *   3. landing on /login + signing in with the new admin
 *   4. arriving at /me/dashboard with the username visible in topbar
 */

import { expect, test } from '@playwright/test';

const ADMIN = {
  username: 'alice',
  email: 'alice@example.com',
  display: 'Alice Wang',
  password: 'AlicePass!2024', // pragma: allowlist secret
};

test('cold start: setup → login → dashboard', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/setup$/);

  await page.getByPlaceholder('Example GPU Lab').fill('Example GPU Lab');
  await page.getByPlaceholder('example-gpu').fill('example-gpu');
  await page.getByRole('button', { name: '下一步' }).click();

  await page.getByPlaceholder('alice').fill(ADMIN.username);
  await page.getByPlaceholder('alice@example.com').fill(ADMIN.email);
  await page.getByPlaceholder('Alice Wang').fill(ADMIN.display);
  const passwordInputs = page.locator('input[type="password"]');
  await passwordInputs.nth(0).fill(ADMIN.password);
  await passwordInputs.nth(1).fill(ADMIN.password);
  await page.getByRole('button', { name: '下一步' }).click();

  await page.getByRole('button', { name: '初始化' }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByPlaceholder(ADMIN.username).fill(ADMIN.username);
  await page.locator('input[type="password"]').fill(ADMIN.password);
  await page.getByRole('button', { name: '登录' }).click();

  await expect(page).toHaveURL(/\/me\/dashboard$/);
  await expect(page.getByText(ADMIN.display)).toBeVisible();
});
