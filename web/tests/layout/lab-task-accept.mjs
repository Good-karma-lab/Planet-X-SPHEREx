// The lab with an open task at five windows (test of 20 Nov 2026, S1, S2, M12): the picture's side with a finding's task, a square's
// task, a square with its lead, and without a task. node tests/layout/lab-task-accept.mjs [BASE] [OUTDIR]. Every call to the API
// sends X-Sky-Check: 1; against a deployed site the tasks it takes are guest tasks, printed with their ids for deletion.

import { chromium } from '@playwright/test';
const BASE = process.argv[2] ?? 'https://www.spherex.space';
const OUT = process.argv[3] ?? 'shots';
const ONLY = process.argv[4] ? process.argv[4].split(',') : null;
const URLS = {
  finding: '/#/lab?ra=52.01586&dec=7.84618&fov=60&task=finding&finding=film:c14d097c45d2',
  square: '/#/lab?ra=50.961&dec=7.25&fov=10&task=cell&cell=291-0151&quarter=NE',
  lead: '/#/lab?ra=50.961&dec=7.25&fov=10&task=cell&cell=291-0151&quarter=NE&lead=film:c14d097c45d2&lead_kind=moves',
  none: '/#/lab?ra=48.437&dec=4.804&fov=20',
};
const SIZES = [[360, 640], [375, 812], [412, 742], [768, 1024], [800, 600]];
const tasks = [];
const rows = [];
const b = await chromium.launch();
try {
  for (const [w, h] of SIZES) {
    const phone = w < 600;
    const ctx = await b.newContext({ viewport: { width: w, height: h }, isMobile: phone, hasTouch: w !== 800, deviceScaleFactor: 1 });
    await ctx.route('**/api/**', (r) => r.continue({ headers: { ...r.request().headers(), 'x-sky-check': '1' } }));
    await ctx.addInitScript(() => localStorage.setItem('spherex-hints-seen-off', '1'));
    for (const [name, u] of Object.entries(URLS)) {
      if (ONLY && !ONLY.includes(name)) continue;
      const p = await ctx.newPage();
      p.on('response', async (r) => {
        if (r.url().includes('/api/tasks/next')) { const j = await r.json().catch(() => null); if (j?.task?.id) tasks.push({ id: j.task.id, at: new Date().toISOString() }); }
      });
      await p.goto(BASE + u, { waitUntil: 'domcontentloaded', timeout: 60000 });
      if (name !== 'none') await p.getByTestId('task-ask').first().waitFor({ timeout: 60000 }).catch(() => {});
      else await p.getByTestId('panel-tabs').waitFor({ timeout: 60000 }).catch(() => {});
      await p.waitForTimeout(9000);
      const m = await p.evaluate(() => {
        const R = (e) => { if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };
        const q = (s) => document.querySelector(s);
        const panel = q('.lab .panel');
        const vis = (s) => [...document.querySelectorAll(s)].filter((e) => { const r = e.getBoundingClientRect(); const pr = panel?.getBoundingClientRect(); return r.height > 0 && (!pr || (r.top >= pr.top - 1 && r.bottom <= pr.bottom + 1 && r.bottom <= innerHeight)); }).length;
        return {
          picture: R(q('[data-testid=picture]')), panel: R(panel), tbar: R(q('.lab .tbar')),
          ask: vis('[data-testid=task-ask]'), answers: vis('.tp-ans, .tp-btns .btn'), allAnswers: document.querySelectorAll('.tp-ans, .tp-btns .btn').length,
        };
      });
      rows.push({ size: `${w}x${h}`, name, side: m.picture ? Math.min(m.picture[2], m.picture[3]) : null, ...m });
      await p.screenshot({ path: `${OUT}/${name}-${w}x${h}.png` });
      await p.close();
    }
    await ctx.close();
  }
} finally { await b.close(); }
console.log(JSON.stringify({ rows, tasks }));
