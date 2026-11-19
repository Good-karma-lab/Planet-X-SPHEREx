// Layout audit: overlapping controls on the main screens at four widths (phone, tablet portrait, tablet, desktop).
// node tests/layout/overlaps.mjs [base] [outdir] [hints]   — base defaults to http://localhost:5190
import { chromium } from '@playwright/test';
import fs from 'node:fs';
const BASE = process.argv[2] ?? 'http://localhost:5190';
const OUT = process.argv[3] ?? 'test-results/layout';
const HINTS = process.argv[4] === 'hints';
fs.mkdirSync(OUT, { recursive: true });
const ALL = { 1440: [1440, 900], 1280: [1280, 800], 1024: [1024, 768], 860: [860, 1000], 700: [700, 900], 600: [600, 900], 375: [375, 812], 844: [844, 390] };
const SIZES = (process.env.SIZES ?? '1440,1024,700,375').split(',').map((k) => ALL[k]);
const PAGES = [
  ['lab', '/#/lab?ra=269.452&dec=4.693&fov=10'],
  ['globe', '/#/explore?ra=269.452&dec=4.693&fov=10'],
  ['findings', '/#/findings'],
  ['planetx', '/#/planetx'],
  ['cabinet', '/#/me'],
  ['landing', '/'],
  ['method', '/#/method'],
  ['about', '/#/about'],
  ['index', '/#/index'],
].filter(([id]) => !process.env.PAGES || process.env.PAGES.split(',').includes(id));
const audit = () => {
  const sel = 'button, a[href], input, select, textarea, [role="button"], [role="tab"], [role="slider"]';
  const vis = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    // the inside of a closed <details> (a menu) is not shown, whatever size the browser gives it
    const d = e.closest('details:not([open])');
    if (d && !e.closest('summary')) return null;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) return null;
    for (let p = e; p; p = p.parentElement) {
      const ps = getComputedStyle(p);
      if (ps.display === 'none' || ps.visibility === 'hidden' || +ps.opacity === 0) return null;
      if (p.hasAttribute('inert') || p.getAttribute('aria-hidden') === 'true') return null;
    }
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return null;
    return r;
  };
  // an element hidden by a clipping ancestor (overflow) does not count where it is clipped
  const clip = (e, r) => {
    let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    for (let p = e.parentElement; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(hidden|auto|scroll|clip)/.test(s.overflow + s.overflowX + s.overflowY)) {
        const q = p.getBoundingClientRect();
        x0 = Math.max(x0, q.left); y0 = Math.max(y0, q.top); x1 = Math.min(x1, q.right); y1 = Math.min(y1, q.bottom);
      }
    }
    return x1 - x0 > 1 && y1 - y0 > 1 ? { left: x0, top: y0, right: x1, bottom: y1 } : null;
  };
  const els = [...document.querySelectorAll(sel)].map((e) => [e, vis(e)]).filter(([, r]) => r).map(([e, r]) => [e, clip(e, r)]).filter(([, r]) => r);
  const name = (e) => (e.getAttribute('aria-label') || e.innerText || e.getAttribute('title') || e.value || e.tagName).trim().replace(/\s+/g, ' ').slice(0, 28) + (e.dataset.testid ? `[${e.dataset.testid}]` : '');
  const out = [];
  for (let i = 0; i < els.length; i++)
    for (let j = i + 1; j < els.length; j++) {
      const [a, ra] = els[i], [b, rb] = els[j];
      if (a.contains(b) || b.contains(a)) continue;
      const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (w > 2 && h > 2) out.push(`${name(a)} × ${name(b)} ${Math.round(w)}×${Math.round(h)}`);
    }
  const small = [...new Set([...document.querySelectorAll('body *')].filter((e) => {
    if (!e.childNodes.length || ![...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) return false;
    const r = e.getBoundingClientRect(); if (r.width < 1 || r.bottom < 0 || r.top > innerHeight) return false;
    const f = parseFloat(getComputedStyle(e).fontSize); return f < 11 && !vis(e) === false;
  }).map((e) => `${parseFloat(getComputedStyle(e).fontSize)}px ${e.innerText.trim().slice(0, 20)}`))];
  const unnamed = [...document.querySelectorAll('button, [role="button"]')].filter((e) => vis(e) && !(e.getAttribute('aria-label') || e.innerText.trim() || e.getAttribute('title'))).length;
  const sig = {};
  const seen = new Map();
  for (const e of document.querySelectorAll('body *')) {
    if (e.closest('svg') && e.tagName !== 'svg') continue;
    const cls = typeof e.className === 'string' ? e.className.trim().split(/\s+/).filter((c) => c && !/^(on|active|busy|loading|playing)$/.test(c)).slice(0, 2).join('.') : '';
    const key0 = e.dataset.testid ? `[${e.dataset.testid}]` : cls ? `${e.tagName.toLowerCase()}.${cls}` : null;
    if (!key0) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const n = (seen.get(key0) ?? 0) + 1; seen.set(key0, n);
    if (n > 3) continue;
    sig[`${key0}#${n}`] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  }
  return { sig, controls: els.length, overlaps: out, small: small.length, smallList: small.slice(0, 40), unnamed };
};
const browser = await chromium.launch();
const report = {};
try {
  for (const [w, h] of SIZES) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1, hasTouch: w < 600, isMobile: w < 600 });
    if (!HINTS) await ctx.addInitScript(() => { try { localStorage.setItem('spherex-tour-seen', '1'); localStorage.setItem('spherex-hints-seen-off', '1'); } catch {} });
    const page = await ctx.newPage();
    for (const [id, path] of PAGES) {
      let ok = false;
      for (let t = 0; t < 3 && !ok; t++) ok = await page.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: 60_000 }).then(() => true, () => false);
      if (!ok) { console.log(`${id}@${w}: did not load`); continue; }
      if (id === 'lab') await page.getByTestId('lab-status').filter({ hasText: /^[A-Z][a-z]{2} ’\d\d/ }).waitFor({ timeout: 120_000 }).catch(() => {});
      await page.waitForTimeout(id === 'lab' ? 2500 : 4000);
      const r = await page.evaluate(audit);
      report[`${id}@${w}`] = r;
      await page.screenshot({ path: `${OUT}/${id}-${w}.png` });
      console.log(`${id}@${w}: ${r.controls} controls, ${r.overlaps.length} overlaps, ${r.small} texts <11px, ${r.unnamed} unnamed buttons`);
      for (const o of r.overlaps) console.log('   ', o);
    }
    await ctx.close();
  }
} finally {
  await browser.close();
}
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
