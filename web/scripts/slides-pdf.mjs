// Exports docs/submission/demo/slides.html to PDF (one 1920 × 1080 page per slide) and a PNG of every slide.
//   cd web && node scripts/slides-pdf.mjs
import { chromium } from '@playwright/test';
import { resolve } from 'node:path';

const html = resolve('../docs/submission/demo/slides.html');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto(`file://${html}`);
await page.waitForTimeout(1000);
await page.pdf({ path: resolve('../docs/submission/demo/spherex-sky-slides.pdf'), width: '1920px', height: '1080px', printBackground: true });
const n = await page.locator('section.slide').count();
for (let i = 0; i < n; i++) await page.locator('section.slide').nth(i).screenshot({ path: resolve(`../docs/submission/demo/out/slide${i + 1}.png`) });
await browser.close();
console.log(`${n} slides`);
