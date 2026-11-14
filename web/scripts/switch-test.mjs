// Every Display switch on and off, on real fields, without reload: the picture (canvas + marks) must change each time, and come back.
// Captures of each state are written to the given folder.
//   cd web && node scripts/switch-test.mjs [base-url] [capture-dir]
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const BASE = process.argv[2] ?? 'https://spherex.space';
const OUT = process.argv[3] ?? '/tmp/switch-test';
mkdirSync(OUT, { recursive: true });
// Interpolate acts only while playing (a cross-fade between frames): it is left out of the still captures
const SWITCHES = ['boostChk', 'satChk', 'sharpChk', 'ringChk', 'trailChk', 'ringsChk', 'movChk', 'anoChk', 'maskChk', 'refChk'];
const only = process.argv[4];
const FIELDS = [
  ['barnard5', '#/lab?ra=269.44629&dec=4.76792&fov=5', /Season \d of \d/],
  ['barnard20', '#/lab?ra=269.44629&dec=4.76792&fov=20', /Season \d of \d/],
  ['ecliptic-days', '#/lab?ra=169.89170&dec=4.35140&fov=60&film=61027', /Days · pass .* visits/],
];
const b = await chromium.launch();
const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
const shot = () =>
  pg.evaluate(() => {
    const t = document.createElement('canvas');
    t.width = 240;
    t.height = 240;
    const x = t.getContext('2d');
    for (const c of document.querySelectorAll('.vsq canvas'))
      if (c.width && getComputedStyle(c).visibility !== 'hidden') x.drawImage(c, 0, 0, 240, 240);
    const d = x.getImageData(0, 0, 240, 240).data;
    let h = 0;
    for (let i = 0; i < d.length; i += 4) h = (h * 31 + d[i] + 7 * d[i + 1] + 13 * d[i + 2]) >>> 0;
    return h;
  });
let fails = 0;
for (const [name, url, ready] of FIELDS.filter((f) => !only || f[0] === only)) {
  await pg.goto('about:blank');
  await pg.goto(BASE + '/' + url);
  await pg.waitForFunction(
    (re) => new RegExp(re).test(document.querySelector('[data-testid=lab-status]')?.textContent ?? ''),
    ready.source,
    { timeout: 180000 },
  );
  await pg.waitForTimeout(6000);
  await pg.keyboard.press('Space'); // paused: the same frame for every capture
  await pg.waitForTimeout(1500);
  for (const id of SWITCHES) {
    const el = pg.locator(`#${id}`);
    if (await el.isDisabled()) {
      const why = await pg.locator(`label:has(#${id})`).getAttribute('title');
      console.log(`${name} ${id}: disabled — ${why?.match(/\(([^)]*)\)$/)?.[1] ?? ''}`);
      continue;
    }
    const a = await shot();
    await pg.locator('.vsq').screenshot({ path: `${OUT}/${name}_${id}_a.png` });
    await el.click({ force: true });
    await pg.waitForTimeout(1500);
    const c = await shot();
    await pg.locator('.vsq').screenshot({ path: `${OUT}/${name}_${id}_b.png` });
    await el.click({ force: true });
    await pg.waitForTimeout(1500);
    const d = await shot();
    const ok = a !== c;
    if (!ok) fails++;
    console.log(`${name} ${id}: ${ok ? 'changes' : 'NO CHANGE'}${a === d ? ', back' : ', not back (live marks?)'}`);
  }
}
await b.close();
process.exit(fails ? 1 : 0);
