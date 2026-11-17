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

  await page.addInitScript(() => localStorage.setItem('spherex-tour-seen', '1')); // the first-visit tour is its own test
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
  await page.goto('/#/lab?ra=269.44629&dec=4.76792&fov=10');
  for (const t of [
    'Controls',
    'Object',
    'Spectrum',
    'Candidates',
    'Picture',
    'Bands',
    'Detectors',
    'Months',
    'Days',
    'Blink',
    'Difference',
    'Co-add',
    'Colour blend',
    'Mono sum',
  ]) {
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

test('a first-time visitor who clicks the rail right after the globe opens stays where they clicked (the tour does not take them back)', async ({
  page,
}) => {
  await page.goto('/#/explore');
  await page.waitForTimeout(600);
  await page.locator('a[href="#/lab"]').first().click();
  await page.waitForTimeout(3500); // past the tour's start (2.5 s)
  await expect(page).toHaveURL(/#\/lab$/);
});
