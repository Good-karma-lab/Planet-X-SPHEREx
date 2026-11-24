// Release 84's checks of the lab in a browser, with the tasks mocked (nothing is created on the service; every call sends
// X-Sky-Check: 1 and only GETs go through): node tests/layout/lab-84-accept.mjs BASE [OUTDIR]. BASE is a dev server (vite) that proxies
// /api to a service with the field of film:8ae52fb8ac8b. Exit 1 when a check fails.
import { chromium } from '@playwright/test';
const BASE = process.argv[2] ?? 'http://localhost:5190';
const OUT = process.argv[3];
const V = (await (await fetch(`${BASE}/api/v1/findings?ids=film:8ae52fb8ac8b`, { headers: { 'X-Sky-Check': '1' } })).json()).finds[0].data.visits;
const step = (id, question, show) => ({ id, steps: [{ id: id === 'track' ? 'steps' : 'q', question, show, answers: [{ id: 'yes', label: 'Yes', result: 'a' }, { id: 'no', label: 'No', result: 'b' }] }] });
const TRACK = { id: 't_mock', kind: 'finding', finding_id: 'film:8ae52fb8ac8b', ra: 46.58709, dec: 12.79007, fov: 20, film: 61073.4, series: step('track', 'Does one dot step along the arrow?', 'blink'), pictures: V.map((v, i) => ({ i, ...v })) };
const PEAK = { id: 't_mock', kind: 'finding', finding_id: 'peak:dd36a21a3e16', ra: 51.744073, dec: 12.764138, fov: 10, series: step('peak', 'It blinks', 'sums') };
let bad = 0;
const check = (size, name, ok, got = '') => { if (!ok) bad++; console.log(`${ok ? 'ok  ' : 'FAIL'} ${size} ${name} ${got}`); };
const b = await chromium.launch({ args: ['--use-gl=angle', '--enable-unsafe-swiftshader'] });
try {
  for (const [w, h, side] of [[1990, 1120, 1024], [1440, 900, 804], [375, 812, 375]]) {
    const size = `${w}x${h}`;
    const phone = w < 600;
    const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: phone, hasTouch: phone });
    let body = TRACK;
    let slow = 0;
    let crash = false;
    await ctx.route('**/src/screens/pages.tsx*', (r) => (crash ? r.fulfill({ contentType: 'text/javascript', body: "export function InfoPage() { throw new Error('a test'); }" }) : r.continue()));
    await ctx.route('**/api/**', async (r) => {
      const q = r.request();
      if (q.url().includes('/api/tasks/next')) return r.fulfill({ json: { task: body } });
      if (q.method() !== 'GET') return r.abort();
      if (slow && q.url().includes('/api/v1/film')) await new Promise((z) => setTimeout(z, slow));
      return r.continue({ headers: { ...q.headers(), 'x-sky-check': '1' } }).catch(() => {});
    });
    await ctx.addInitScript(() => localStorage.setItem('spherex-hints-seen-off', '1'));
    const p = await ctx.newPage();
    const errs = [];
    p.on('pageerror', (e) => errs.push(String(e)));
    const shot = (n) => (OUT ? p.screenshot({ path: `${OUT}/84-${n}-${w}.png` }) : null);
    const ev = (f, a) => p.evaluate(f, a);
    // B9: the film of the pass is slow: the loading card with its video
    slow = 6000;
    const trackUrl = '/#/lab?ra=46.58709&dec=12.79007&fov=20&task=finding&kind=finding&finding=film:8ae52fb8ac8b&find=film:8ae52fb8ac8b';
    await p.goto(BASE + trackUrl + '&film=61073.4');
    await p.waitForTimeout(4500);
    check(size, 'B9 the video while the visits load', (await p.locator('.loadov video').count()) === 1);
    await shot('loading');
    slow = 0;
    await p.waitForFunction(() => document.querySelector('[aria-label=Visit]')?.disabled === false, null, { timeout: 120000 });
    await p.waitForTimeout(3000);
    const nv = await ev(() => Number(document.querySelector('[aria-label=Visit]').max));
    const px = () => ev(() => { const c = document.querySelector('canvas.ov'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 120 && d[i] > 200 && d[i + 1] > 150 && d[i + 1] < 230 && d[i + 2] < 170) n++; return n; });
    const words = [];
    for (let k = 0; k <= nv; k++) {
      await ev((k) => { const e = document.querySelector('[aria-label=Visit]'); e.value = String(k); e.dispatchEvent(new Event('input', { bubbles: true })); }, k);
      await p.waitForTimeout(450);
      words.push(await p.locator('.zoombox span').textContent());
      if (k === 12 || k === 13) await shot(`visit${k + 1}`);
    }
    const own = words.map((x, k) => (/· visit/.test(x) ? k + 1 : 0)).filter(Boolean);
    check(size, 'A1 the zoom says its own visit on the track’s visits only', JSON.stringify(own) === '[5,9,10,14,15]', own.join());
    check(size, 'A1 and the number of the frame shown', words.every((x, k) => !/· visit/.test(x) || x.endsWith(`visit ${k + 1}`)));
    check(size, 'A1 one sentence under the question', /No.? is a fine answer/.test(await p.getByTestId('task-hint').first().textContent()));
    const r0 = await ev(() => { const r = document.querySelector('[data-testid=picture]').getBoundingClientRect(); return Math.round(Math.min(r.width, r.height)); });
    check(size, 'the picture keeps its size', r0 >= side, r0);
    // B8
    await p.locator('.acts > summary').click();
    await p.waitForTimeout(400);
    check(size, 'B8 the menu is in sight and takes a click', await ev(() => { const r = document.querySelector('.acts-menu').getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && !!document.elementFromPoint(r.x + r.width / 2, r.y + r.height - 14)?.closest('.acts-menu'); }));
    await shot('menu');
    await p.locator('.acts > summary').click();
    // B5, B6, B10
    await ev(() => document.querySelector('[data-testid=ptab-view]').click());
    await p.waitForTimeout(500);
    await shot('view');
    for (const id of ['redChk', 'anoChk', 'archChk'])
      check(size, `B6/B10 ${id}: off, says Months only, has the way there`, await ev((i) => { const e = document.getElementById(i); const l = e.closest('label'); return e.disabled && !e.checked && /Months only/.test(l.innerText) && !!l.querySelector(`[data-testid=${i}-go]`) && !/\d/.test(l.innerText); }, id));
    const on = await px();
    await ev(() => document.getElementById('trailChk').click());
    await p.waitForTimeout(900);
    const off = await px();
    check(size, 'B5 Paths off takes the task’s path away', off < on * 0.6, `${on} -> ${off}`);
    await ev(() => document.getElementById('peopleChk').click());
    await ev(() => { const e = document.querySelector('[data-testid=fov]'); e.value = '10'; e.dispatchEvent(new Event('change', { bubbles: true })); });
    await p.waitForTimeout(1500);
    check(size, 'N7 another field size keeps the switches', /people=0/.test(await ev(() => location.hash)) && /traj=0/.test(await ev(() => location.hash)));
    await ev(() => document.querySelector('[data-testid=redChk-go]').click());
    await p.waitForTimeout(800);
    const hash = await ev(() => location.hash);
    check(size, 'B6 the way there: Months with the red sources on', /red=1/.test(hash) && !/film=/.test(hash.split('finding=')[0] + hash.split('&find=')[1]?.replace(/^[^&]*/, '')), hash.slice(0, 160));
    // A3, B7
    body = PEAK;
    await p.goto(BASE + '/#/lab?ra=51.74407&dec=12.76414&fov=10&task=finding&kind=finding&finding=peak:dd36a21a3e16&find=peak:dd36a21a3e16');
    await p.reload();
    await p.waitForTimeout(16000);
    check(size, 'A3 the sum stands on the picture', (await p.locator('[data-testid=peak-sum] img').count()) === 1);
    await shot('peak');
    await p.getByTestId('peak-sum-button').click();
    await p.waitForTimeout(800);
    check(size, 'A3 one button gives the sky back', (await p.locator('[data-testid=peak-sum] img').count()) === 0);
    const bx = await p.getByTestId('picture').boundingBox();
    await p.mouse.click(bx.x + bx.width * 0.42, bx.y + bx.height * 0.48); // the bright star beside the ring
    await p.waitForTimeout(1200);
    const obj = await ev(() => ({ tab: document.querySelector('.ptabs [aria-selected=true]').textContent, blind: !!document.querySelector('[data-testid=obj-blind]'), more: document.querySelector('[data-testid=obj-more]')?.style.display !== 'none' }));
    check(size, 'B7 another object opens its own card', /Object/.test(obj.tab) && !obj.blind && obj.more, JSON.stringify(obj));
    await shot('object');
    // A4
    crash = true;
    await p.goto(BASE + '/#/method');
    await p.reload();
    await p.waitForTimeout(1500);
    check(size, 'A4 a screen that throws says so, the rail stays', (await p.getByTestId('screen-failed').count()) === 1 && (await p.getByTestId('nav-lab').count()) === 1);
    await ev(() => (location.hash = '#/me'));
    await p.waitForTimeout(1200);
    check(size, 'A4 and the others open', (await p.getByTestId('screen-failed').count()) === 0);
    crash = false; // back on the screen that failed: it is made anew (its module is asked for again only by a reload, so the one that threw throws again)
    await ev(() => (location.hash = '#/method'));
    await p.waitForTimeout(1200);
    const again = await p.getByTestId('screen-failed').count();
    await ev(() => (location.hash = '#/index'));
    await p.waitForTimeout(800);
    await p.reload();
    await p.waitForTimeout(1500);
    check(size, 'A4 a failed screen is tried anew on return, and a reload heals it', again === 1 && (await p.getByTestId('screen-failed').count()) === 0);
    check(size, 'no uncaught errors', errs.every((e) => e === 'Error: a test'), errs.slice(0, 2).join(' | '));
    await ctx.close();
  }
} finally {
  await b.close();
}
process.exit(bad ? 1 : 0);
