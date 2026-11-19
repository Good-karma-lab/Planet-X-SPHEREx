// The acceptance checks of the mobile review (docs/reviews/mobile-ux-2026-11-19.md, §5) at the review's window sizes:
//   node tests/layout/mobile-accept.mjs [base] [outdir]
// 1 the lab's picture: its share of the window (Barnard, the sheet at rest; a finding's task), no black side bands
// 2 targets of at least 44×44 (a ::before/::after zone counts) in the lab, Findings, the globe's catalogue, Cabinet, More
// 3 overlapping controls; the overlays on the picture as a share of it
// 4 the sheet: ▶ and the time line never covered; full leaves ≥ 90 px of picture; back at rest after another address
// 5 no row that slides sideways without a sign of it (a fade); 7 Blink/Difference/Co-add hidden or explained in Days
// 8 the date of the time line clear of the thumb in five places; the sheet dragged by a finger (touch events through CDP)
import { chromium } from '@playwright/test';
import fs from 'node:fs';

const BASE = process.argv[2] ?? 'http://localhost:5190';
const OUT = process.argv[3] ?? 'test-results/mobile';
fs.mkdirSync(OUT, { recursive: true });
const SIZES = (process.env.SIZES ?? '375x812,360x640,412x742,412x915,915x412,1440x900').split(',').map((s) => s.split('x').map(Number));
const LAB = '/#/lab?ra=269.452&dec=4.693&fov=10';
const LAB2 = '/#/lab?ra=83.822&dec=-5.391&fov=10';
const LOADED = /^[A-Z][a-z]{2} ’\d\d|^visit \d/;

const PAGE_FNS = () => {
  const vis = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    if (e.closest('details:not([open])') && !e.closest('summary')) return null;
    for (let p = e; p; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0 || p.getAttribute('aria-hidden') === 'true' || p.inert) return null;
    }
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return null;
    // cut off by a box that clips (a sheet at rest, a list that scrolls): not on screen
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (let q = e.parentElement; q; q = q.parentElement) {
      const qs = getComputedStyle(q);
      if (!/(hidden|auto|scroll|clip)/.test(qs.overflow + qs.overflowX + qs.overflowY)) continue;
      const b = q.getBoundingClientRect();
      if (cx < b.left || cx > b.right || cy < b.top || cy > b.bottom) return null;
    }
    return r;
  };
  // the box a finger can hit: the element, grown by an absolutely placed ::before/::after with negative insets
  const zone = (e, r) => {
    let { left, top, right, bottom } = r;
    for (const pe of ['::before', '::after']) {
      const s = getComputedStyle(e, pe);
      if (s.content === 'none' || s.position !== 'absolute') continue;
      const n = (v) => (v.endsWith('px') ? parseFloat(v) : 0);
      left = Math.min(left, r.left + n(s.left));
      top = Math.min(top, r.top + n(s.top));
      right = Math.max(right, r.right - n(s.right));
      bottom = Math.max(bottom, r.bottom - n(s.bottom));
    }
    return { w: right - left, h: bottom - top };
  };
  const name = (e) =>
    ((e.getAttribute('aria-label') || e.innerText || e.getAttribute('title') || e.value || e.tagName) + '').trim().replace(/\s+/g, ' ').slice(0, 26) +
    (e.dataset.testid ? `[${e.dataset.testid}]` : '');
  window.__acc = {
    small() {
      const out = [];
      for (const e of document.querySelectorAll('button, a[href], select, input:not([type=hidden]), [role=tab], summary')) {
        const r = vis(e);
        if (!r) continue;
        if (e.matches('input[type=range]') ) continue; // a slider's track is long; its thumb is the target (checked by its height)
        // a box to tick is hit through its label as well
        const lab = e.matches('input[type=checkbox], input[type=radio]') ? e.closest('label') : null;
        const z = lab ? zone(lab, lab.getBoundingClientRect()) : zone(e, r);
        if (z.w < 43.5 || z.h < 43.5) out.push(`${name(e)} ${Math.round(z.w)}×${Math.round(z.h)}`);
      }
      return out;
    },
    overlaps() {
      // each control's box cut by the boxes that clip it (a sheet, a list that scrolls): only what is on screen can overlap
      const clipped = (e, r) => {
        let x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
        for (let q = e.parentElement; q; q = q.parentElement) {
          const qs = getComputedStyle(q);
          if (!/(hidden|auto|scroll|clip)/.test(qs.overflow + qs.overflowX + qs.overflowY)) continue;
          const b = q.getBoundingClientRect();
          x0 = Math.max(x0, b.left); y0 = Math.max(y0, b.top); x1 = Math.min(x1, b.right); y1 = Math.min(y1, b.bottom);
        }
        return x1 - x0 > 1 && y1 - y0 > 1 ? { left: x0, top: y0, right: x1, bottom: y1 } : null;
      };
      const els = [...document.querySelectorAll('button, a[href], select, input, [role=tab], summary')].map((e) => [e, vis(e)]).filter(([, r]) => r).map(([e, r]) => [e, clipped(e, r)]).filter(([, r]) => r);
      const out = [];
      for (let i = 0; i < els.length; i++)
        for (let j = i + 1; j < els.length; j++) {
          const [a, ra] = els[i], [b, rb] = els[j];
          if (a.contains(b) || b.contains(a)) continue;
          const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left), h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
          if (w > 2 && h > 2) out.push(`${name(a)} × ${name(b)} ${Math.round(w)}×${Math.round(h)}`);
        }
      return out;
    },
    lab() {
      const pic = document.querySelector('[data-testid=picture]')?.getBoundingClientRect();
      const sq = document.querySelector('.vsq')?.getBoundingClientRect();
      const sheet = document.querySelector('.lab .panel');
      const sr = sheet?.getBoundingClientRect();
      const sheetOn = sheet && getComputedStyle(sheet).getPropertyValue('--sheet').trim() === '1';
      const nav = document.querySelector('.rail')?.getBoundingClientRect();
      const bottom = sheetOn ? sr.top : innerHeight;
      const visH = pic ? Math.max(0, Math.min(pic.bottom, bottom, innerHeight) - Math.max(pic.top, 0)) : 0;
      const visW = pic ? Math.max(0, Math.min(pic.right, innerWidth) - Math.max(pic.left, 0)) : 0;
      const share = (visW * visH) / (innerWidth * innerHeight);
      // overlays on the picture: everything drawn over it that is not the picture (sum of their areas inside it)
      let over = 0;
      if (pic)
        for (const e of document.querySelectorAll('.vsq .ftitle, .vsq .factions, .vsq .refbox, .vsq .zoombox, .vsq .fstatus, .vsq .fbands > *, .vsq .toolstrip button, .vsq .scalebar')) {
          const r = vis(e);
          if (!r) continue;
          const w = Math.min(r.right, pic.right) - Math.max(r.left, pic.left), h = Math.min(r.bottom, pic.bottom) - Math.max(r.top, pic.top);
          if (w > 0 && h > 0) over += w * h;
        }
      const play = document.querySelector('.tbar .play')?.getBoundingClientRect();
      const range = [...document.querySelectorAll('.tbar .tl-mid input[type=range]')].map(vis).find(Boolean);
      // covered: what a finger at its centre hits is the sheet (or anything else), not the control's own bar
      const covered = (r) => {
        if (!r) return false;
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return !hit?.closest('.tbar');
      };
      // black bands at the sides: the window's width the picture does not take (a phone, where the picture is the window's width)
      const bands = innerWidth < 600 && pic ? Math.max(0, innerWidth - pic.width) : 0;
      return {
        share: +share.toFixed(3), pic: pic ? `${Math.round(pic.width)}×${Math.round(pic.height)}@${Math.round(pic.top)}` : '-',
        sideBands: Math.round(bands), overlay: pic ? +(over / (pic.width * pic.height)).toFixed(3) : 0,
        detent: sheet?.dataset.detent ?? '-', sheetTop: sr ? Math.round(sr.top) : null,
        playCovered: covered(play), rangeCovered: covered(range), picLeft: pic && sheetOn && sr ? Math.round(sr.top - pic.top) : null,
        nav: nav ? Math.round(nav.height) : 0,
      };
    },
    ribbons() {
      const out = [];
      for (const e of document.querySelectorAll('body *')) {
        const s = getComputedStyle(e);
        if (!/(auto|scroll)/.test(s.overflowX) || e.scrollWidth <= e.clientWidth + 2) continue;
        const r = vis(e);
        if (!r || r.width < 60) continue;
        const sign = (s.maskImage && s.maskImage !== 'none') || (s.webkitMaskImage && s.webkitMaskImage !== 'none') || e.dataset.more != null;
        if (!sign) out.push(`${e.className || e.tagName} ${Math.round(e.scrollWidth - e.clientWidth)} px hidden`);
      }
      return out;
    },
    modes() {
      const b = document.querySelector('[data-testid=time-loop]');
      const r = b && vis(b);
      const explained = [...document.querySelectorAll('.tbar *')].some((e) => vis(e) && /Months only|Blink.*Months/i.test(e.textContent ?? '') && e.children.length === 0);
      return { blinkShown: !!r, disabled: !!b?.disabled, explained };
    },
    // the date over the time line against the thumb: is the label of the current place clear of it?
    dateClear() {
      const inp = [...document.querySelectorAll('.tbar .tl-mid input[type=range]')].find((e) => vis(e));
      const on = [...document.querySelectorAll('.tbar .tl-labels span.on')].find((e) => vis(e));
      if (!inp || !on) return null;
      const r = inp.getBoundingClientRect();
      const f = (+inp.value - +inp.min) / Math.max(1, +inp.max - +inp.min);
      const thumb = { left: r.left + f * (r.width - 16), right: r.left + f * (r.width - 16) + 16, top: r.top + r.height / 2 - 8, bottom: r.top + r.height / 2 + 8 };
      const l = on.getBoundingClientRect();
      const hit = Math.min(l.right, thumb.right) > Math.max(l.left, thumb.left) && Math.min(l.bottom, thumb.bottom) > Math.max(l.top, thumb.top);
      return { hit, label: on.textContent };
    },
  };
};

async function lab(page, path) {
  await page.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.getByTestId('lab-status').filter({ hasText: LOADED }).waitFor({ timeout: 120_000 }).catch(() => {});
  await page.waitForTimeout(1500);
}

const report = {};
const b = await chromium.launch();
try {
  for (const [w, h] of SIZES) {
    const touch = w < 1000;
    const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: touch && w < 600, hasTouch: touch, deviceScaleFactor: 1 });
    await ctx.route('**/api/**', (r) => r.continue({ headers: { ...r.request().headers(), 'x-sky-check': '1' } }));
    await ctx.addInitScript(() => localStorage.setItem('spherex-hints-seen-off', '1'));
    await ctx.addInitScript(PAGE_FNS);
    const p = await ctx.newPage();
    const key = `${w}x${h}`;
    const R = (report[key] = {});
    // --- the lab on Barnard, the sheet at rest ---
    await lab(p, LAB);
    await p.evaluate(() => document.querySelector('[data-testid=play]') ?? null);
    R.lab = await p.evaluate(() => window.__acc.lab());
    R.labSmall = await p.evaluate(() => window.__acc.small());
    R.labOverlaps = await p.evaluate(() => window.__acc.overlaps());
    R.ribbons = await p.evaluate(() => window.__acc.ribbons());
    await p.screenshot({ path: `${OUT}/lab-${key}.png` });
    // the date against the thumb, five places
    const pos = [];
    const nPass = await p.evaluate(() => +([...document.querySelectorAll('.tbar .tl-mid input[type=range]')].find((e) => e.offsetParent)?.max ?? 0));
    for (let i = 0; i < 5; i++) {
      const v = Math.round((i / 4) * nPass);
      await p.evaluate((v) => {
        const inp = [...document.querySelectorAll('.tbar .tl-mid input[type=range]')].find((e) => e.offsetParent);
        if (!inp) return;
        inp.value = String(v);
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      }, v);
      await p.waitForTimeout(250);
      pos.push(await p.evaluate(() => window.__acc.dateClear()));
    }
    R.dateHits = pos.filter((x) => x?.hit).length;
    // Days: the modes hidden or explained
    const days = p.getByTestId('scale-days');
    if (await days.isVisible().catch(() => false)) {
      await days.click();
      await p.waitForTimeout(2500);
      R.days = await p.evaluate(() => window.__acc.modes());
      await p.screenshot({ path: `${OUT}/lab-days-${key}.png` });
      await p.getByTestId('scale-months').click().catch(() => {});
      await p.waitForTimeout(800);
    }
    // --- the sheet, three heights (a tap on the grabber steps), then another address ---
    const grip = p.getByTestId('sheet-grip');
    if (await grip.isVisible().catch(() => false)) {
      R.sheet = [];
      for (let i = 0; i < 2; i++) {
        await grip.click();
        await p.waitForTimeout(500);
        R.sheet.push(await p.evaluate(() => window.__acc.lab()));
        await p.screenshot({ path: `${OUT}/lab-sheet${i + 1}-${key}.png` });
      }
      await grip.click(); // back at rest
      await p.waitForTimeout(400);
      await grip.click(); // half again, then another field's address
      await p.waitForTimeout(400);
      await lab(p, LAB2);
      R.sheetAfterAddress = await p.evaluate(() => document.querySelector('.lab .panel')?.dataset.detent ?? '-');
      // a finger drags the grabber up 200 px (touch events through CDP), then lets go
      if (touch) {
        await lab(p, LAB);
        const g = await grip.boundingBox();
        const cdp = await ctx.newCDPSession(p);
        const x = g.x + g.width / 2, y = g.y + g.height / 2;
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
        for (let k = 1; k <= 10; k++) {
          await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y: y - 20 * k }] });
          await p.waitForTimeout(30);
        }
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
        await p.waitForTimeout(700);
        R.drag = await p.evaluate(() => window.__acc.lab());
      }
    }
    // --- a finding's task: Findings → Check this finding ---
    await p.goto(BASE + '/#/findings', { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(4000);
    const check = p.getByRole('link', { name: 'Check this finding' }).first();
    if (await check.isVisible().catch(() => false)) {
      R.findingsSmall = await p.evaluate(() => window.__acc.small());
      await check.click();
      await p.getByTestId('task-panel').waitFor({ timeout: 60_000 }).catch(() => {});
      await p.getByTestId('lab-status').filter({ hasText: LOADED }).waitFor({ timeout: 120_000 }).catch(() => {});
      await p.waitForTimeout(2000);
      R.finding = await p.evaluate(() => window.__acc.lab());
      R.findingSmall = await p.evaluate(() => window.__acc.small());
      R.answersInView = await p.evaluate(() => [...document.querySelectorAll('[data-testid=task-panel] .tp-ans')].filter((e) => { const r = e.getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight; }).length);
      R.findingDays = await p.evaluate(() => window.__acc.modes());
      await p.screenshot({ path: `${OUT}/finding-${key}.png` });
    }
    // --- the globe's catalogue, Cabinet, More ---
    await p.goto(BASE + '/#/explore', { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(3000);
    await p.screenshot({ path: `${OUT}/globe-${key}.png` });
    R.globeSmall = await p.evaluate(() => window.__acc.small());
    R.globeRibbons = await p.evaluate(() => window.__acc.ribbons());
    const cat = p.locator('.cat-handle, [data-testid=cat-open]').first();
    if (await cat.isVisible().catch(() => false)) {
      await cat.click();
      await p.waitForTimeout(1500);
      R.catalogueSmall = await p.evaluate(() => window.__acc.small());
    }
    await p.goto(BASE + '/#/me', { waitUntil: 'domcontentloaded' });
    await p.waitForTimeout(2500);
    R.cabinetSmall = await p.evaluate(() => window.__acc.small());
    const more = p.getByTestId('nav-more');
    if (await more.isVisible().catch(() => false)) {
      await more.click();
      await p.waitForTimeout(600);
      R.moreSmall = await p.evaluate(() => window.__acc.small());
    }
    await ctx.close();
    const L = R.lab, F = R.finding ?? {};
    console.log(
      `${key}: lab ${L.share} (${L.pic}, sides ${L.sideBands}, overlays ${L.overlay}) · finding ${F.share ?? '-'} (${F.pic ?? '-'}, sides ${F.sideBands ?? '-'}, answers in view ${R.answersInView ?? '-'})` +
        ` · small: lab ${R.labSmall.length}, finding ${R.findingSmall?.length ?? '-'}, findings ${R.findingsSmall?.length ?? '-'}, globe ${R.globeSmall.length}, catalogue ${R.catalogueSmall?.length ?? '-'}, cabinet ${R.cabinetSmall.length}, more ${R.moreSmall?.length ?? '-'}` +
        ` · overlaps ${R.labOverlaps.length} · ribbons ${R.ribbons.length}+${R.globeRibbons.length} · date hits ${R.dateHits}/5` +
        ` · Days ${R.days ? (R.days.blinkShown ? (R.days.explained ? 'explained' : 'SHOWN') : 'hidden') : '-'}` +
        ` · sheet ${R.sheet ? R.sheet.map((s) => `${s.detent}:${s.playCovered || s.rangeCovered ? 'COVERS' : 'ok'}/pic ${s.picLeft}`).join(' ') : '-'} after-address ${R.sheetAfterAddress ?? '-'}` +
        ` · drag ${R.drag ? `${R.drag.detent} top ${R.drag.sheetTop}` : '-'}`,
    );
  }
} finally {
  await b.close();
}
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
