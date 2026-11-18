import { expect, test } from '@playwright/test';

// The test service runs on an empty store: no exposure index, so the coverage endpoints answer 503 and the survey tiles may be
// unreachable. Those failed loads are expected; any other console error fails the test.
const expected = (t: string) => /Failed to load resource/.test(t);

test('the rail switches screens by clicking', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error' && !expected(m.text())) errors.push(m.text());
  });
  page.on('pageerror', (e) => errors.push(e.message));

  await page.addInitScript(() => localStorage.setItem('spherex-hints-seen-off', '1')); // the newcomer's hints are their own test
  await page.goto('/#/explore');
  await expect(page.getByTestId('sky')).toBeVisible();
  const heading: Record<string, RegExp> = { Findings: /^Findings$/, Index: /^Exposure index$/, Method: /SPHEREx files/, About: /^About$/ };
  for (const name of ['Lab', 'Findings', 'Index', 'Method', 'About', 'Globe']) {
    await page.getByRole('link', { name, exact: true }).click();
    if (name === 'Lab') await expect(page.getByText('Open a place from the globe')).toBeVisible();
    else if (name === 'Globe') await expect(page.getByTestId('sky')).toBeVisible();
    else await expect(page.locator('main h1').first()).toHaveText(heading[name]!);
    await expect(page.getByRole('link', { name, exact: true })).toHaveAttribute('aria-current', 'page');
  }
  expect(errors).toEqual([]);
});

test('a field opens the lab with its controls', async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto('/#/lab?ra=269.44629&dec=4.76792&fov=10');
  // lab-v2: the panel is three tabs (Task, Object, View); the picture's own controls below it
  for (const t of ['Task', 'Object', 'View']) await expect(page.getByRole('tab', { name: t, exact: true })).toBeVisible();
  for (const t of ['Months', 'Days', 'Blink', 'Difference', 'Co-add']) {
    await expect(page.getByRole('button', { name: t, exact: true })).toBeVisible();
  }
  await expect(page.getByTestId('band-ch4')).toContainText(/Methane|CH₄/);
});

test('a link opens its screen directly', async ({ page }) => {
  await page.goto('/#/method');
  await expect(page.locator('main h1').first()).toHaveText(/SPHEREx files/);
  await page.goto('/#/findings');
  await expect(page.locator('main h1').first()).toHaveText('Findings');
});

test('no horizontal overflow at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('a first-time visitor who clicks the rail right after the globe opens stays where they clicked (the hints never take them anywhere)', async ({
  page,
}) => {
  await page.goto('/#/explore');
  await page.waitForTimeout(600);
  await page.locator('a[href="#/lab"]').first().click();
  await page.waitForTimeout(3500); // past the moment a hint appears (0.9 s)
  await expect(page).toHaveURL(/#\/lab$/);
});
