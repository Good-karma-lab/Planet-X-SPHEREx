// Findings at six window sizes (desktop, laptop, tablet upright and on its side, phones): node tests/layout/findings-shots.mjs <base> <outdir> [open]
import { chromium } from '@playwright/test';
import fs from 'node:fs';
const [, , BASE = 'http://localhost:5190', OUT = 'test-results/findings', open] = process.argv;
fs.mkdirSync(OUT, { recursive: true });
const SIZES = [[1920, 1080], [1440, 900], [1180, 820], [1024, 768], [412, 915], [375, 812]];
const b = await chromium.launch();
for (const [w, h] of SIZES) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: w < 600, hasTouch: w < 600, deviceScaleFactor: 1 });
  await ctx.route('**/api/**', (r) => r.continue({ headers: { ...r.request().headers(), 'x-sky-check': '1' } }));
  await ctx.addInitScript(() => localStorage.setItem('spherex-hints-seen-off', '1'));
  const p = await ctx.newPage();
  await p.goto(BASE + '/#/findings', { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await p.waitForTimeout(6000);
  const top = await p.evaluate(() => Math.round(document.querySelector('.fgrid > li')?.getBoundingClientRect().top ?? -1));
  await p.screenshot({ path: `${OUT}/findings-${w}x${h}.png` });
  let note = '';
  if (open) {
    const btn = p.getByTestId('filters-open');
    if (await btn.isVisible()) { await btn.click(); await p.waitForTimeout(600); await p.screenshot({ path: `${OUT}/findings-${w}x${h}-filters.png` }); note = ' filters shot'; }
  }
  console.log(`${w}x${h}: first card at y=${top}${note}`);
  await ctx.close();
}
await b.close();
