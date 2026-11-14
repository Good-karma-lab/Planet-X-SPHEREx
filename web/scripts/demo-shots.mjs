// Screenshots of the live tool for the slides (1920 × 1080, no subtitles).
//   cd web && node scripts/demo-shots.mjs [base-url] [out-dir]
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const BASE = process.argv[2] ?? 'https://spherex.space';
const OUT = process.argv[3] ?? '../docs/submission/demo/img';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const ready = () =>
  page.waitForFunction(() => /Season \d of \d/.test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''), null, { timeout: 120000 });

await page.goto(`${BASE}/#/globe`);
await page.waitForTimeout(8000);
await page.screenshot({ path: `${OUT}/globe.png` });

await page.goto(`${BASE}/#/lab?ra=269.44629&dec=4.76792&fov=10`);
await ready();
await page.waitForTimeout(5000);
await page.screenshot({ path: `${OUT}/lab-barnard.png` });

await page.locator('.archsw input').check();
await page.waitForTimeout(400);
await page.screenshot({ path: `${OUT}/archive.png` });
await page.locator('.archsw input').uncheck();

const vsq = await page.locator('.vsq').boundingBox();
await page.mouse.click(vsq.x + vsq.width * 0.514, vsq.y + vsq.height * 0.33);
await page.waitForFunction(() => /points from/.test(document.querySelector('.spec-state')?.textContent ?? ''), null, { timeout: 120000 });
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}/spectrum.png` });

await page.goto(`${BASE}/#/lab?ra=331.09645&dec=-56.80059&fov=20&ref=0`);
await page.getByRole('button', { name: 'Controls', exact: true }).click();
await ready();
await page.getByText('Anomaly 1', { exact: true }).first().click();
await page.waitForTimeout(3000);
await page.screenshot({ path: `${OUT}/anomaly.png` });

await page.goto(`${BASE}/#/findings`);
await page.waitForTimeout(8000);
await page.screenshot({ path: `${OUT}/findings.png` });
await browser.close();
