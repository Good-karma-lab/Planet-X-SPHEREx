// Records the demo video of the live tool: a scripted browser visits https://spherex.space and the scenes are cut to 30 s.
// Every picture is a screen recording of the tool on real SPHEREx data; the subtitles are drawn on the page during the recording.
//   cd web && node scripts/demo-video.mjs [base-url] [out-dir]
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync, renameSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const BASE = process.argv[2] ?? 'https://spherex.space';
const OUT = process.argv[3] ?? '../docs/submission/demo/out';
mkdirSync(OUT, { recursive: true });
const W = 1920;
const H = 1080;

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, recordVideo: { dir: OUT, size: { width: W, height: H } } });
const t0 = Date.now();
const page = await ctx.newPage();
const marks = [];
const mark = (name) => marks.push({ name, t: (Date.now() - t0) / 1000 });

async function subtitle(text) {
  await page.evaluate((t) => {
    let el = document.getElementById('demo-sub');
    if (!el) {
      el = document.createElement('div');
      el.id = 'demo-sub';
      el.style.cssText =
        'position:fixed;left:50%;bottom:44px;transform:translateX(-50%);z-index:9999;max-width:1400px;padding:12px 26px;border-radius:14px;' +
        'background:rgba(4,10,14,.82);border:1px solid rgba(95,227,214,.5);color:#eafcff;font:600 34px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;' +
        'text-align:center;box-shadow:0 10px 40px rgba(0,0,0,.6);pointer-events:none';
      document.body.appendChild(el);
    }
    el.textContent = t;
  }, text);
}
async function waitPicture() {
  // the lab is ready when its status line names a season
  await page.waitForFunction(() => /Season \d of \d/.test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''), null, { timeout: 120000 });
}

// 1. globe
await page.goto(`${BASE}/#/globe`);
await page.waitForTimeout(7000);
await subtitle("NASA's SPHEREx maps the whole sky in 102 infrared colours.");
mark('globe');
const sky = page.getByTestId('sky');
const box = await sky.boundingBox();
for (let i = 0; i < 12; i++) {
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -120);
  await page.waitForTimeout(250);
}
await page.waitForTimeout(800);
mark('globe-end');

// 2. Barnard's Star, 10′: the seasons as a pendulum, the mover ring and its Gaia check
await page.goto(`${BASE}/#/lab?ra=269.44629&dec=4.76792&fov=10`);
await waitPicture();
await page.waitForTimeout(4000);
await subtitle('SPHEREx Sky plays the seasons back and forth: what moves, jumps.');
mark('pendulum');
await page.waitForTimeout(6000);
mark('pendulum-end');

// 3. archive images, then SPHEREx
await page.locator('.archsw input').check();
await subtitle('Decades of motion: archive surveys from 1985, then SPHEREx 2025–26.');
mark('archive');
await page.waitForTimeout(5600);
mark('archive-end');
await page.locator('.archsw input').uncheck();

// 4. a click on a star: its spectrum, measured live
const vsq = await page.locator('.vsq').boundingBox();
await page.mouse.click(vsq.x + vsq.width * 0.514, vsq.y + vsq.height * 0.33);
await subtitle('Click any source: its spectrum, measured live in every exposure.');
mark('spectrum');
await page.waitForTimeout(7000);
mark('spectrum-end');

// 5. ε Indi B, 20′: the anomaly search ranks the brown dwarf first
await page.goto(`${BASE}/#/lab?ra=331.09645&dec=-56.80059&fov=20&ref=0`);
await page.getByRole('button', { name: 'Controls', exact: true }).click();
await waitPicture();
await page.getByText('Anomaly 1', { exact: true }).first().waitFor({ timeout: 60000 });
await page.waitForTimeout(1500);
await page.getByText('Anomaly 1', { exact: true }).first().click();
await subtitle('Anomaly search: a cold brown dwarf stands out by its spectrum.');
mark('anomaly');
await page.waitForTimeout(6000);
mark('anomaly-end');

// 6. title card
await page.evaluate(() => {
  document.getElementById('demo-sub')?.remove();
  const el = document.createElement('div');
  el.style.cssText =
    'position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;' +
    'background:radial-gradient(1200px 700px at 50% 40%,#0d2430,#04080c);color:#eafcff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';
  el.innerHTML =
    '<div style="font-size:96px;font-weight:700;letter-spacing:.02em">SPHEREx Sky</div>' +
    '<div style="font-size:46px;font-weight:500;color:#9ff3ea">see the sky change</div>' +
    '<div style="font:600 56px ui-monospace,Menlo,monospace;color:#ffffff;margin-top:24px">spherex.space</div>' +
    '<div style="font-size:28px;font-weight:500;color:#a0b9c2;margin-top:20px">Real NASA SPHEREx data · Good Karma Lab · NASA Space Apps 2026</div>';
  document.body.appendChild(el);
});
mark('title');
await page.waitForTimeout(3500);
mark('title-end');

await ctx.close();
await browser.close();
const webm = readdirSync(OUT).filter((f) => f.endsWith('.webm') && f !== 'raw.webm');
renameSync(join(OUT, webm[0]), join(OUT, 'raw.webm'));
writeFileSync(join(OUT, 'marks.json'), JSON.stringify(marks, null, 1));
console.log(JSON.stringify(marks));
