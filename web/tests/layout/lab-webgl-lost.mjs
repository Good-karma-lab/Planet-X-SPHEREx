// The lab when the graphics card takes its context away (a driver restart, a machine that slept): the picture comes back by itself, in
// Months and in Days, and another field opens (before 24 Nov 2026 the whole lab went black). node tests/layout/lab-webgl-lost.mjs BASE
// [RA DEC]. BASE proxies /api to a service; only GETs go through, every call sends X-Sky-Check: 1. Exit 1 when a check fails.
import { chromium } from '@playwright/test';
const [BASE = 'http://localhost:5190', RA = '46.58709', DEC = '12.79007'] = process.argv.slice(2);
let bad = 0;
const b = await chromium.launch({ args: ['--use-gl=angle', '--enable-unsafe-swiftshader'] });
try {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.route('**/api/**', (r) => (r.request().method() === 'GET' && !r.request().url().includes('/api/tasks/') ? r.continue({ headers: { ...r.request().headers(), 'x-sky-check': '1' } }) : r.abort()));
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 160)));
  p.on('console', (m) => /screen failed/.test(m.text()) && errs.push(m.text().slice(0, 160)));
  const lose = () => p.evaluate(() => document.querySelector('canvas.vid').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext());
  const check = async (n) => {
    const s = await p.evaluate(() => {
      const c = document.querySelector('canvas.vid');
      const g = c?.getContext('webgl2');
      let lit = 0;
      if (g && !g.isContextLost()) {
        const d = new Uint8Array(c.width * c.height * 4);
        g.readPixels(0, 0, c.width, c.height, g.RGBA, g.UNSIGNED_BYTE, d);
        for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 150) lit++;
      }
      return { failed: !!document.querySelector('[data-testid=screen-failed]'), lost: g?.isContextLost() ?? null, lit };
    });
    const ok = !s.failed && s.lost === false && s.lit > 200 && !errs.length;
    if (!ok) bad++;
    console.log(ok ? 'ok  ' : 'FAIL', n, JSON.stringify(s), errs.splice(0).join(' | '));
  };
  const go = (f) => p.evaluate(f);
  await p.goto(`${BASE}/#/lab?ra=${RA}&dec=${DEC}&fov=20`);
  await p.waitForTimeout(11000);
  await check('the field');
  await lose();
  await p.waitForTimeout(9000);
  await check('Months, the context lost: the picture is back');
  await go(() => (location.hash += '&film=61073.4'));
  await p.waitForTimeout(11000);
  await check('Days');
  await lose();
  await p.waitForTimeout(11000);
  await check('Days, the context lost: the visits are back');
  await lose();
  await go(() => (location.hash = location.hash.replace('&film=61073.4', '').replace('fov=20', 'fov=60')));
  await p.waitForTimeout(14000);
  await check('the context lost as another field opens');
} finally {
  await b.close();
}
process.exit(bad ? 1 : 0);
