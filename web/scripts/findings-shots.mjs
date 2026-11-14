// Pictures for the Findings cards: the lab's picture of each test target's 20′ field (real SPHEREx stacks, the marks as the lab draws
// them), saved as JPEG in public/findings/.
//   cd web && node scripts/findings-shots.mjs [base-url]
import { chromium } from '@playwright/test';

const BASE = process.argv[2] ?? 'https://spherex.space';
const OUT = new URL('../public/findings/', import.meta.url).pathname;
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1300, height: 900 }, deviceScaleFactor: 1 });
const cat = await (await fetch(`${BASE}/catalog.json`)).json();
for (const o of cat.catalog.filter((x) => x.b)) {
  const dt = 2025.9 - 2000;
  const dec = o.dec + ((o.pmdec ?? 0) * dt) / 3.6e6;
  const ra = (o.ra + ((o.pmra ?? 0) * dt) / 3.6e6 / Math.cos((((dec + o.dec) / 2) * Math.PI) / 180) + 360) % 360;
  await pg.goto(`${BASE}/#/lab?ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&fov=20`);
  await pg.waitForFunction(() => /Season \d of \d/.test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''), null, { timeout: 180000 });
  await pg.keyboard.press('Space');
  await pg.waitForTimeout(9000);
  const slug = o.n.normalize('NFKD').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
  await pg.locator('.vsq').screenshot({ path: `${OUT}${slug}.jpg`, type: 'jpeg', quality: 82 });
  console.log(slug);
}
await b.close();
