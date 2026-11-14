import { expect, test } from '@playwright/test';

test('the shell loads, talks to the service and switches screens by clicking', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(e.message));

  await page.goto('/');
  await expect(page.getByTestId('status')).toHaveText(/^sky service \d+\.\d+\.\d+$/);
  for (const name of ['Lab', 'Findings', 'Method', 'Globe']) {
    await page.getByRole('link', { name, exact: true }).click();
    if (name === 'Lab') await expect(page.locator('.lab-empty')).toBeVisible();
    else if (name === 'Globe') await expect(page.getByText('Test targets')).toBeVisible();
    else await expect(page.locator('main h1')).toHaveText(name);
    await expect(page.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page');
  }
  expect(errors).toEqual([]);
});

test('a link opens its screen directly', async ({ page }) => {
  await page.goto('/#/method');
  await expect(page.locator('main h1')).toHaveText('Method');
});

test('no horizontal overflow at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
