// The product flow walked with fingers, on a phone or tablet window: landing → Start searching → a square on the globe → the lab
// with the task → a mark put by sliding under the magnifier → the task closed → Findings → Check this finding → Cabinet.
// Touches are real touch events (Chromium: tap, slide and two-finger pinch through the DevTools protocol).
//
//   node scripts/mobile-flow.mjs --out <dir> [--url http://localhost:5187] [--size p390]
//
// Prints one line per step, PASS or FAIL with the reason; exits 1 when a step fails. Every browser started here is closed.
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { SIZES } from './mobile-sizes.mjs';

const arg = (k, d) => {
  const i = process.argv.indexOf(`--${k}`);
  return i < 0 ? d : process.argv[i + 1];
};
const URL = arg('url', 'http://localhost:5187');
const OUT = arg('out', 'test-results/mobile-flow');
const sizeKey = arg('size', 'p390');
const S = SIZES[sizeKey];
const LOADED = /^[A-Z][a-z]{2} ’\d\d/;
const dir = join(OUT, sizeKey);
mkdirSync(dir, { recursive: true });

const browser = await chromium.launch({ channel: 'chromium', args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
let failed = 0;
try {
  const ctx = await browser.newContext({ viewport: { width: S.width, height: S.height }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await ctx.addInitScript(() => localStorage.setItem('spherex-tour-seen', '1'));
  const page = await ctx.newPage();
  page.setDefaultTimeout(30000);
  const cdp = await ctx.newCDPSession(page);
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  const touch = (type, pts) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: pts.map((p, i) => ({ x: p[0], y: p[1], id: i, radiusX: 12, radiusY: 12, force: 1 })) });
  const slide = async (from, to, steps = 12, hold = null) => {
    await touch('touchStart', [from]);
    for (let i = 1; i <= steps; i++) {
      await touch('touchMove', [[from[0] + ((to[0] - from[0]) * i) / steps, from[1] + ((to[1] - from[1]) * i) / steps]]);
      await page.waitForTimeout(16);
    }
    if (hold) await hold();
    await touch('touchEnd', []);
  };
  const pinch = async (c, d0, d1, steps = 14) => {
    const at = (d) => [[c[0] - d / 2, c[1]], [c[0] + d / 2, c[1]]];
    await touch('touchStart', [at(d0)[0]]);
    await touch('touchStart', at(d0));
    for (let i = 1; i <= steps; i++) {
      await touch('touchMove', at(d0 + ((d1 - d0) * i) / steps));
      await page.waitForTimeout(16);
    }
    await touch('touchEnd', []);
  };
  const shot = (name) => page.screenshot({ path: join(dir, `${name}.png`) });
  const scrollY = () => page.evaluate(() => Math.round(window.scrollY + (document.scrollingElement?.scrollTop ?? 0)));
  const hscroll = () => page.evaluate(() => document.scrollingElement.scrollWidth - window.innerWidth);
  const step = async (name, fn) => {
    try {
      const note = await fn();
      const h = await hscroll();
      if (h > 0) throw new Error(`the page scrolls sideways by ${h} px`);
      console.log(`PASS ${name}${note ? ` — ${note}` : ''}`);
    } catch (e) {
      failed++;
      console.log(`FAIL ${name} — ${String(e.message ?? e).split('\n')[0].slice(0, 260)}`);
      await shot(`FAIL-${name.replace(/\W+/g, '-')}`).catch(() => {});
    }
  };
  const box = async (loc) => {
    const b = await loc.boundingBox();
    if (!b) throw new Error('not on the page');
    return b;
  };
  const zoomValue = () => page.evaluate(() => Number(document.querySelector('[data-testid="zoom-slider"] input')?.value ?? NaN));

  await step('landing: Start searching opens the globe', async () => {
    await page.goto(`${URL}/`);
    await page.getByTestId('landing-start').tap();
    await page.getByTestId('sky').waitFor();
    await page.waitForTimeout(4000);
    await shot('01-globe');
    return page.url().split('#')[1];
  });

  await step('globe: one finger turns it and the page does not move', async () => {
    const b = await box(page.getByTestId('sky'));
    const y0 = await scrollY();
    const before = await page.getByTestId('sky').screenshot();
    await slide([b.x + b.width * 0.5, b.y + b.height * 0.55], [b.x + b.width * 0.5, b.y + b.height * 0.3]);
    await page.waitForTimeout(600);
    const after = await page.getByTestId('sky').screenshot();
    if ((await scrollY()) !== y0) throw new Error('the page scrolled under the globe');
    if (before.equals(after)) throw new Error('the globe did not turn');
  });

  await step('globe: two fingers zoom it', async () => {
    const b = await box(page.getByTestId('sky'));
    const z0 = await zoomValue();
    await pinch([b.x + b.width / 2, b.y + b.height / 2], 80, 300);
    await page.waitForTimeout(600);
    const z1 = await zoomValue();
    await pinch([b.x + b.width / 2, b.y + b.height / 2], 300, 120);
    await page.waitForTimeout(600);
    const z2 = await zoomValue();
    await shot('02-globe-zoomed');
    if (!(z1 > z0)) throw new Error(`spreading two fingers did not zoom in (${z0} → ${z1})`);
    if (!(z2 < z1)) throw new Error(`closing two fingers did not zoom out (${z1} → ${z2})`);
    return `zoom ${z0.toFixed(2)} → ${z1.toFixed(2)} → ${z2.toFixed(2)}`;
  });

  await step('globe: Coverage on, a tap on a square opens its card as a sheet', async () => {
    const t = page.getByTestId('cov-toggle');
    if ((await t.getAttribute('aria-pressed')) !== 'true') await t.tap();
    await page.waitForTimeout(1500);
    const b = await box(page.getByTestId('sky'));
    await page.touchscreen.tap(b.x + b.width / 2, b.y + b.height * 0.4);
    await page.getByTestId('cov-card').waitFor({ timeout: 10000 });
    await page.waitForTimeout(500);
    await shot('03-square-card');
    const c = await box(page.getByTestId('cov-card'));
    const take = await box(page.getByTestId('cov-take'));
    if (take.height < 44) throw new Error(`"Take this square" is ${take.height} px high`);
    if (c.y + c.height > S.height + 1) throw new Error('the card runs under the window');
  });

  await step('globe → lab: Take this square opens the lab with the task on screen', async () => {
    await page.getByTestId('cov-take').tap();
    await page.getByTestId('task-panel').waitFor({ timeout: 40000 });
    await page.getByTestId('lab-status').filter({ hasText: LOADED }).waitFor({ timeout: 900000 });
    await page.waitForTimeout(1500);
    await shot('04-lab-task');
    const p = await box(page.getByTestId('picture'));
    const k = await box(page.getByTestId('task-panel'));
    const nav = await box(page.getByTestId('nav-lab'));
    for (const id of ['task-nothing', 'task-marks']) {
      const b = await page.getByTestId(id).boundingBox();
      if (b && b.y + b.height > nav.y + 1 && nav.y > S.height / 2) throw new Error(`${id} lies under the bar`);
    }
    if (k.y + k.height > S.height) throw new Error('the task panel runs under the window');
    return `picture ${Math.round(p.width)} px of ${S.width}`;
  });

  let marks0 = 0;
  await step('lab: a mark is put by sliding under the magnifier, where the finger lifts', async () => {
    marks0 = await page.evaluate(() => JSON.parse(localStorage.getItem('spherex-marks') ?? '[]').length).catch(() => 0);
    const mark = page.getByTestId('touch-mark');
    if ((await mark.getAttribute('aria-pressed')) !== 'true') await mark.tap();
    const p = await box(page.getByTestId('picture'));
    const from = [p.x + p.width * 0.35, p.y + p.height * 0.6];
    const to = [p.x + p.width * 0.55, p.y + p.height * 0.45];
    let glass = null;
    await slide(from, to, 12, async () => {
      glass = await page.locator('canvas.loupe').boundingBox();
      await shot('05-magnifier');
    });
    if (!glass) throw new Error('no magnifier while the finger was down');
    if (glass.y + glass.height > to[1] - 10) throw new Error('the magnifier is not above the finger');
    await page.getByTestId('mark-editor').waitFor({ timeout: 8000 });
    await page.waitForTimeout(400);
    await shot('06-mark-editor');
    const ed = await box(page.getByTestId('mark-editor'));
    if (ed.y < to[1] + 12) throw new Error('the editor covers the point of the mark');
    await page.getByTestId('mark-label').fill('Touch mark');
    return `editor at ${Math.round(ed.y)} px, the point at ${Math.round(to[1])} px`;
  });

  await step('lab: the sheet goes up by a tap on its grabber and by a drag, the picture keeps its size', async () => {
    // the editor is closed first (its own close button)
    const x = page.getByTestId('mark-editor').locator('button').first();
    await x.tap();
    await page.getByTestId('mark-editor').waitFor({ state: 'hidden' });
    const p0 = await box(page.getByTestId('picture'));
    const grip = page.getByTestId('sheet-grip');
    const h = () => page.evaluate(() => Math.round(document.querySelector('.lab .panel').getBoundingClientRect().height));
    const h0 = await h();
    await grip.tap();
    await page.waitForTimeout(500);
    const h1 = await h();
    const g = await box(grip);
    await slide([g.x + g.width / 2, g.y + g.height / 2], [g.x + g.width / 2, 90], 10);
    await page.waitForTimeout(600);
    const h2 = await h();
    await shot('07-sheet-full');
    const p1 = await box(page.getByTestId('picture'));
    if (!(h1 > h0 + 40)) throw new Error(`a tap on the grabber did not raise the sheet (${h0} → ${h1})`);
    if (!(h2 > h1 + 40)) throw new Error(`a drag did not raise the sheet (${h1} → ${h2})`);
    if (Math.abs(p1.width - p0.width) > 0.5 || Math.abs(p1.y - p0.y) > 0.5) throw new Error('the picture moved when the sheet opened');
    const g2 = await box(grip);
    await slide([g2.x + g2.width / 2, g2.y + g2.height / 2], [g2.x + g2.width / 2, S.height - 40], 10);
    await page.waitForTimeout(600);
    const h3 = await h();
    if (Math.abs(h3 - h0) > 2) throw new Error(`the sheet did not come back down (${h0} → ${h3})`);
    return `sheet ${h0} → ${h1} → ${h2} → ${h3} px`;
  });

  await step('lab: the task is closed with "Done, with marks"', async () => {
    const done = page.getByTestId('task-marks');
    await done.waitFor();
    if (await done.isDisabled()) throw new Error('"Done, with marks" is off although a mark was put');
    const before = page.url();
    await done.tap();
    await page.waitForFunction((u) => location.href !== u || /No more tasks|did not answer/.test(document.querySelector('[data-testid="task-line"]')?.textContent ?? ''), before, { timeout: 30000 });
    await page.waitForTimeout(800);
    await shot('08-task-closed');
    return (await page.getByTestId('task-line').innerText().catch(() => '')).trim() || 'the next task opened';
  });

  await step('Findings: one column of cards, "Check this finding" opens the lab task', async () => {
    await page.getByTestId('nav-findings').tap();
    const go = page.getByRole('link', { name: 'Check this finding' }).first();
    await go.waitFor({ timeout: 40000 });
    await page.waitForTimeout(2500);
    await shot('09-findings');
    const cards = await page.locator('.fcard').evaluateAll((els) => els.slice(0, 6).map((e) => Math.round(e.getBoundingClientRect().left)));
    const cols = new Set(cards).size;
    const want = S.width < 600 ? 1 : S.width < 840 ? 2 : 3;
    if (cols !== want) throw new Error(`${cols} columns of cards, ${want} wanted`);
    await go.scrollIntoViewIfNeeded();
    const b = await box(go);
    if (b.height < 44) throw new Error(`"Check this finding" is ${b.height} px high`);
    for (let i = 0; i < 5 && !/#\/lab\?/.test(page.url()); i++) {
      await go.tap({ timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(800);
    }
    await page.getByTestId('task-panel').waitFor({ timeout: 40000 });
    await shot('10-lab-finding-task');
    return `${cols} column${cols > 1 ? 's' : ''}`;
  });

  await step('Cabinet: sign in by the e-mail link, the tabs slide, the open tab is on screen', async () => {
    await page.getByTestId('nav-me').tap();
    const signin = page.getByTestId('signin');
    if (await signin.isVisible().catch(() => false)) {
      const mail = page.getByLabel('E-mail address');
      await mail.tap();
      await mail.fill('mobile-flow@example.org');
      const m = await mail.evaluate((e) => ({ type: e.type, auto: e.autocomplete, font: parseFloat(getComputedStyle(e).fontSize), h: e.getBoundingClientRect().height }));
      if (m.type !== 'email' || m.font < 16 || m.h < 44) throw new Error(`the e-mail field: type ${m.type}, ${m.font} px text, ${m.h} px high`);
      await page.getByRole('button', { name: 'Send me a sign-in link' }).tap();
      await page.getByTestId('dev-link').tap();
    }
    await page.getByTestId('who-name').waitFor();
    for (const t of ['marks', 'notifications']) {
      await page.getByTestId(`tab-${t}`).scrollIntoViewIfNeeded();
      await page.getByTestId(`tab-${t}`).tap();
      await page.getByTestId(`panel-${t}`).waitFor();
      await page.waitForTimeout(500);
      const b = await box(page.getByTestId(`tab-${t}`));
      if (b.x < 0 || b.x + b.width > S.width) throw new Error(`the open tab "${t}" is not fully on screen`);
    }
    await page.getByTestId('tab-marks').tap();
    await page.waitForTimeout(600);
    await shot('11-cabinet-marks');
    const has = await page.getByTestId('panel-marks').innerText();
    return /Touch mark/.test(has) ? 'the mark put by touch is in My marks' : 'signed in (the mark was put as a guest before the sign-in)';
  });

  if (errors.length) console.log(`page errors: ${[...new Set(errors)].slice(0, 5).join(' | ')}`);
  await ctx.close();
} finally {
  await browser.close();
}
console.log(failed ? `${failed} step(s) failed` : 'the whole flow passed');
process.exit(failed ? 1 : 0);
