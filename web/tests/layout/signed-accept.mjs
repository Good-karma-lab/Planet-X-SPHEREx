// The signed-in person's path against a local build (API 8790 with SKY_AUTH_DEV=1 and a product database of its own, vite 5190): an
// answer of every kind of task is given once, the next task opens, a mark comes and goes, an unfinished task waits in the Cabinet, two
// tabs on one task count once, a card opened in Findings never touches the clipboard. Never against a deployed site: it signs in and
// answers. node tests/layout/signed-accept.mjs DB [OUTDIR] [only,names]. Exit 1 when a check fails.
import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
const [DB, OUT, ONLY] = process.argv.slice(2);
const BASE = 'http://localhost:5190';
const sql = (q) => execSync(`sqlite3 ${JSON.stringify(DB)} ${JSON.stringify(q)}`).toString().trim();
let bad = 0;
const check = (n, ok, got = '') => { if (!ok) bad++; console.log(`${ok ? 'ok  ' : 'FAIL'} ${n} ${typeof got === 'string' ? got : JSON.stringify(got)}`); };
const want = (n) => !ONLY || ONLY.split(',').includes(n);
const b = await chromium.launch({ args: ['--use-gl=angle', '--enable-unsafe-swiftshader'] });
try {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => {
    localStorage.setItem('spherex-hints-seen-off', '1');
    window.__clip = [];
    const real = navigator.clipboard;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: (t) => (window.__clip.push(['writeText', t]), Promise.resolve()), write: (t) => (window.__clip.push(['write', String(t)]), Promise.resolve()), readText: () => real?.readText?.() } });
    const ex = document.execCommand.bind(document);
    document.execCommand = (c, ...a) => (window.__clip.push(['execCommand', c]), ex(c, ...a));
    document.addEventListener('copy', () => window.__clip.push(['copy event']));
  });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', (e) => errs.push(String(e).slice(0, 200)));
  const posts = [];
  ctx.on('response', (r) => { if (r.request().method() === 'POST' && /\/done$/.test(new URL(r.url()).pathname)) posts.push(r.status()); });
  await p.goto(BASE + '/#/me');
  await p.getByLabel('E-mail', { exact: true }).fill(`signed-${Date.now().toString(36)}@example.org`);
  await p.getByRole('button', { name: 'Continue with e-mail' }).click();
  await p.getByTestId('dev-link').click();
  await p.getByTestId('who-name').waitFor({ timeout: 20000 });
  const uid = sql("select id from acc_users order by created desc limit 1");
  const shot = (n) => (OUT ? p.screenshot({ path: `${OUT}/signed-${n}.png` }) : null);
  const answers = () => Number(sql(`select count(*) from acc_series_answers where user_id='${uid}'`));
  const enabled = (pg, ms = 280000) => pg.waitForFunction(() => [...document.querySelectorAll('.tp-ans')].some((e) => !e.disabled), null, { timeout: ms });
  /** answers every question with its first answer until the task is recorded */
  const walk = async (pg) => {
    for (let k = 0; k < 6; k++) {
      await enabled(pg);
      await pg.waitForTimeout(400);
      const pic = pg.getByTestId('task-pictures').locator('button').first();
      if (await pic.count()) await pic.click();
      await pg.locator('.tp-ans:not([disabled])').first().click();
      await pg.waitForTimeout(1500);
      if (await pg.getByTestId('task-done').count()) return true;
    }
    return false;
  };
  const FINDS = { peak: ['peak:2cc94068c566', 269.463443, 4.763804], red: ['red:18847f8dd5dc', 331.6782, -56.4409], mover: ['mover:6909a2ee0124', 331.0967, -56.8007], anomaly: ['anomaly:cc50ea7d2f74', 331.0473, -56.7806], film: ['film:aed36bc0f2e7', 60.6089, 8.3985] };
  for (const kind of ['peak', 'mover', 'red', 'anomaly', 'film']) {
    if (!want(kind)) continue;
    const [id, ra, dec] = FINDS[kind];
    // as a card's address: task=finding with no task id (the case the tester asked about)
    const n0 = answers();
    posts.length = 0;
    const t0 = Date.now();
    await p.goto(`${BASE}/#/lab?ra=${ra}&dec=${dec}&fov=10&find=${encodeURIComponent(id)}&find_ra=${ra}&find_dec=${dec}&task=finding&finding=${encodeURIComponent(id)}`);
    await p.reload();
    await p.getByTestId('task-ask').waitFor({ timeout: 60000 }).catch(() => {});
    try {
      const done = await walk(p);
      check(`${kind}: answered`, done, `${Math.round((Date.now() - t0) / 1000)} s`);
      check(`${kind}: one answer in the database, one request`, answers() === n0 + 1 && posts.length === 1 && posts[0] === 200, { rows: answers() - n0, posts });
      const next = p.getByTestId('task-next');
      const square = p.getByTestId('task-done').getByTestId('give-square-button');
      const has = [await next.count(), await square.count()];
      check(`${kind}: the next task or a square is offered`, has[0] + has[1] === 1, has);
      await shot(kind);
      if (has[0]) {
        const h = await p.evaluate(() => location.hash);
        await next.click();
        await p.waitForTimeout(2500);
        check(`${kind}: Next opens another task`, (await p.evaluate(() => location.hash)) !== h && (await p.getByTestId('task-done').count()) === 0);
      }
      // the answered task opened again and answered again: still one
      await p.goto(`${BASE}/#/lab?ra=${ra}&dec=${dec}&fov=10&find=${encodeURIComponent(id)}&find_ra=${ra}&find_dec=${dec}&task=finding&finding=${encodeURIComponent(id)}`);
      await p.reload();
      await p.getByTestId('task-ask').waitFor({ timeout: 60000 }).catch(() => {});
      if (await walk(p).catch(() => false)) check(`${kind}: answered twice counts once`, answers() === n0 + 1, answers() - n0);
    } catch (e) {
      check(`${kind}: reached the end`, false, String(e).slice(0, 160));
      await shot(`${kind}-fail`);
    }
  }
  if (want('cell')) {
    await p.goto(BASE + '/#/me');
    await p.getByTestId('next-square').click();
    await p.getByTestId('task-ask').first().waitFor({ timeout: 60000 }).catch(() => {});
    const t = sql(`select id||' '||cell_id||' '||quarter from acc_tasks where user_id='${uid}' and kind='cell' and state='open' limit 1`);
    check('cell: a quarter is given', /^t_/.test(t), t);
    // left unfinished: it waits in the Cabinet as the same task
    await p.goto(BASE + '/#/me');
    await p.waitForTimeout(2500);
    check('cell: an unfinished square waits in the Cabinet', /Continue/.test(await p.getByTestId('next-square').textContent()));
    await p.getByTestId('next-square').click();
    await p.getByTestId('task-ask').first().waitFor({ timeout: 60000 }).catch(() => {});
    check('cell: it is the same task', sql(`select count(*) from acc_tasks where user_id='${uid}' and kind='cell'`) === '1');
    posts.length = 0;
    const nothing = p.getByTestId('task-nothing');
    const ok = await p.waitForFunction(() => { const e = document.querySelector('[data-testid=task-nothing]'); return e && !e.disabled; }, null, { timeout: 420000 }).then(() => true, () => false);
    check('cell: after a minute and every pass it can be closed', ok);
    if (ok) {
      await nothing.click();
      await p.getByTestId('task-done').waitFor({ timeout: 20000 }).catch(() => {});
      check('cell: recorded once', posts.length === 1 && sql(`select state from acc_tasks where id='${t.split(' ')[0]}'`) === 'done', posts);
      check('cell: the look is counted', sql(`select count(*) from grid_views where viewer='user:${uid}'`) === '1');
      await shot('cell');
    }
  }
  if (want('mark')) {
    await p.goto(BASE + '/#/lab?ra=269.44629&dec=4.76792&fov=10');
    await p.waitForFunction(() => /^[A-Z][a-z]{2} ’\d\d/.test(document.querySelector('[data-testid=lab-status]')?.innerText ?? ''), null, { timeout: 240000 });
    await p.getByTestId('tool-mark').click();
    check('mark: the tool says what it waits for, whole', /Click a place/.test(await p.getByTestId('tool-tip').textContent()));
    const bx = await p.getByTestId('picture').boundingBox();
    await p.keyboard.press('Escape');
    check('mark: Esc leaves the tool, nothing is put', (await p.getByTestId('tool-tip').count()) === 0 && sql(`select count(*) from acc_marks where user_id='${uid}'`) === '0');
    await p.getByTestId('tool-mark').click();
    await p.mouse.click(bx.x + bx.width * 0.3, bx.y + bx.height * 0.6);
    await p.getByTestId('flow-card').waitFor({ timeout: 5000 });
    await p.waitForTimeout(2500);
    check('mark: kept in the account', sql(`select count(*) from acc_marks where user_id='${uid}'`) === '1');
    await shot('mark');
    const del = p.getByTestId('flow-card').getByRole('button', { name: /delete|remove/i });
    if (await del.count()) {
      p.once('dialog', (d) => d.accept());
      await del.first().click();
      await p.waitForTimeout(2500);
      check('mark: taken back, gone from the account', sql(`select count(*) from acc_marks where user_id='${uid}'`) === '0');
    } else check('mark: it can be taken back', false, 'no button');
  }
  if (want('tabs')) {
    const [id, ra, dec] = FINDS.peak;
    sql(`delete from acc_series_answers where user_id='${uid}'`);
    const url = `${BASE}/#/lab?ra=${ra}&dec=${dec}&fov=10&task=finding&finding=${encodeURIComponent(id)}&find=${encodeURIComponent(id)}`;
    const q = await ctx.newPage();
    for (const pg of [p, q]) {
      await pg.goto(url);
      await pg.reload();
      await pg.getByTestId('task-ask').waitFor({ timeout: 60000 });
      await enabled(pg);
    }
    const r = [await walk(p), await walk(q).catch(() => false)];
    check('two tabs: both end, one answer', r[0] && r[1] && answers() === 1, { r, rows: answers() });
    await q.close();
  }
  if (want('clip')) {
    await p.goto(BASE + '/#/findings');
    await p.locator('.fcard').first().waitFor({ timeout: 60000 });
    await p.evaluate(() => (window.__clip.length = 0));
    const n = Math.min(4, await p.locator('.fcard').count());
    for (let k = 0; k < n; k++) {
      await p.locator('.fcard').nth(k).click({ position: { x: 60, y: 60 } }).catch(() => {});
      await p.waitForTimeout(1500);
      await p.keyboard.press('Escape');
      await p.goto(BASE + '/#/findings');
      await p.waitForTimeout(1200);
    }
    for (const t of ['dblclick', 'contextmenu']) await p.locator('.fcard').first().dispatchEvent(t);
    check('findings: opening a card never touches the clipboard', (await p.evaluate(() => window.__clip.length)) === 0, await p.evaluate(() => window.__clip));
  }
  check('no uncaught errors', errs.length === 0, errs.slice(0, 2));
} finally {
  await b.close();
}
process.exit(bad ? 1 : 0);
