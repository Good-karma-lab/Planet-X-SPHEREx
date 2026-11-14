// A smoke test of the deployed site: every screen and search on real data, the way a visitor would use them.
//   cd web && node scripts/smoke-live.mjs [base-url]
// Prints one line per check (ok / FAIL with the reason) and exits non-zero when any check fails.
import { chromium } from '@playwright/test';

const BASE = process.argv[2] ?? 'https://spherex.space';
const results = [];
const check = async (name, fn) => {
  const t0 = Date.now();
  try {
    const note = await fn();
    results.push({ name, ok: true, note: note ?? '', s: (Date.now() - t0) / 1000 });
  } catch (e) {
    results.push({ name, ok: false, note: String(e).split('\n')[0], s: (Date.now() - t0) / 1000 });
  }
};
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 940 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('response', (r) => {
  if (r.status() >= 500) errors.push(`${r.status()} ${r.url()}`);
});
const season = () =>
  page.waitForFunction(() => /Season \d of \d/.test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''), null, {
    timeout: 120000,
  });
const lab = async (q) => {
  await page.goto(`${BASE}/#/lab?${q}`);
  await page.getByRole('button', { name: 'Controls', exact: true }).click();
  await season();
};

await check('globe: catalogue and test targets', async () => {
  await page.goto(`${BASE}/#/globe`);
  // the catalogue is a drawer, closed by default
  if (!(await page.getByTestId('chip-bench').isVisible())) await page.getByRole('button', { name: 'Catalogue', exact: true }).click();
  await page.getByTestId('chip-bench').waitFor({ timeout: 30000 });
  return await page.getByTestId('chip-bench').innerText();
});
await check('globe: SPHEREx background', async () => {
  await page.getByRole('button', { name: 'SPHEREx — DR1', exact: true }).click();
  await page.waitForTimeout(3000);
});
await check('lab: Barnard 5′ opens (warm)', async () => {
  await lab('ra=269.44629&dec=4.76792&fov=5');
  return await page.getByTestId('lab-status').innerText();
});
await check('lab: movers find Barnard with Gaia', async () => {
  await page.getByTestId('tab-cand').click();
  // the Gaia check: the same motion, or the measured rate's tension with Gaia in σ (a saturated core at 5′)
  await page.getByText(/Gaia ✓|Gaia: same motion|σ apart/).first().waitFor({ timeout: 60000 });
  return (await page.locator('.mvrow').first().innerText()).replace(/\n/g, ' ');
});
await check('lab: spectrum of a clicked star', async () => {
  const v = await page.locator('.vsq').boundingBox();
  await page.mouse.click(v.x + v.width * 0.514, v.y + v.height * 0.165);
  await page.waitForFunction(() => /points from|Measuring/.test(document.querySelector('.spec-state')?.textContent ?? ''), null, {
    timeout: 90000,
  });
  return await page.locator('.spec-state').innerText();
});
await check('lab: ε Indi B 20′ anomaly 1 is the brown dwarf', async () => {
  await lab('ra=331.09645&dec=-56.80059&fov=20');
  await page.getByTestId('tab-cand').click(); // the results of the searches are in the Candidates tab
  await page.getByText('Anomaly 1', { exact: true }).first().waitFor({ timeout: 60000 });
  const row = (await page.locator('.anrow').allInnerTexts()).find((t) => t.startsWith('Anomaly 1')) ?? '';
  if (!/eps Ind B/.test(row)) throw new Error(`anomaly 1 is ${row}`);
  return row.replace(/\n/g, ' ');
});
await check('lab: red sources in WISE 0855 5′', async () => {
  await lab('ra=133.75999&dec=-7.24220&fov=5');
  // the searches start by themselves; their results are in the Candidates tab
  await page.getByTestId('tab-cand').click();
  await page.getByText('Red source 1', { exact: true }).first().waitFor({ timeout: 90000 });
  return (await page.locator('.anrow').allInnerTexts())
    .filter((t) => t.startsWith('Red'))
    .map((t) => t.replace(/\n/g, ' '))
    .join(' / ');
});
await check('lab: 3I/ATLAS film with known bodies and the NEOSSat card', async () => {
  await page.goto(`${BASE}/#/lab?ra=166.82740&dec=5.27870&fov=60&film=61023.6`);
  await page.getByText('Also seen by NEOSSat (CSA)').waitFor({ timeout: 180000 });
});
await check('lab: Days scale of the ecliptic field with known asteroids', async () => {
  await page.goto('about:blank');
  await page.goto(`${BASE}/#/lab?ra=169.89170&dec=4.35140&fov=60&film=61027`);
  await page.waitForFunction(
    () =>
      /Days · pass .* 24 visits .* known asteroids predicted/.test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''),
    null,
    {
      timeout: 180000,
    },
  );
  return (await page.getByTestId('lab-status').innerText()).slice(0, 140);
});
await check('findings: live cards', async () => {
  await page.goto(`${BASE}/#/findings`);
  await page.getByText('Gaia: same motion').first().waitFor({ timeout: 60000 });
});
await check('method and about', async () => {
  await page.goto(`${BASE}/#/method`);
  await page.getByText('Known bodies', { exact: true }).waitFor({ timeout: 20000 });
  await page.goto(`${BASE}/#/about`);
  await page.locator('main h1').waitFor({ timeout: 20000 });
});
await check('no page errors and no 5xx', async () => {
  if (errors.length) throw new Error(errors.slice(0, 5).join(' | '));
});
await browser.close();
for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'} ${r.name} (${r.s.toFixed(1)} s)${r.note ? ` — ${r.note}` : ''}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
