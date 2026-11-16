// Captures of every screen at phone and tablet sizes with touch emulation, plus measurements for the audit
// (horizontal scroll, touch targets under 44 px, text under 12 px, things cut off by the window).
//
//   node scripts/mobile-shots.mjs --out <dir> [--url http://localhost:5187] [--sizes p390,t820] [--only lab,globe] [--engine chromium|webkit]
//
// Nothing is faked: the pages run on the real service behind --url. Every browser started here is closed.
import { chromium, webkit } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { SIZES } from './mobile-sizes.mjs';

const arg = (k, d) => {
  const i = process.argv.indexOf(`--${k}`);
  return i < 0 ? d : process.argv[i + 1];
};
const URL = arg('url', 'http://localhost:5187');
const OUT = arg('out', 'test-results/mobile');
const ENGINE = arg('engine', 'chromium');
const ONLY = arg('only', '').split(',').filter(Boolean);
const GEOM = process.argv.includes('--geom');

const sizes = arg('sizes', 'p375,p390,l812,l844,t768,t820,t1024,t1180').split(',');

const LAB = '#/lab?ra=331.09645&dec=-56.80059&fov=5';
const LOADED = /^[A-Z][a-z]{2} ’\d\d/;
const EMAIL = 'mobile-audit@example.org';

/** What the rules of the study can measure on a page as it stands. */
const measure = () => {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const doc = document.scrollingElement;
  const seen = (e) => {
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) return null;
    const s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0 || s.pointerEvents === 'none') return null;
    // covered by something else at its centre: not a target the user can reach now
    const top = document.elementFromPoint(Math.min(vw - 1, Math.max(0, r.left + r.width / 2)), Math.min(vh - 1, Math.max(0, r.top + r.height / 2)));
    if (top && !(e === top || e.contains(top) || top.contains(e))) return null;
    return r;
  };
  const name = (e) =>
    (e.getAttribute('data-testid') || e.getAttribute('aria-label') || e.textContent || e.getAttribute('title') || e.className || e.tagName)
      .toString()
      .trim()
      .replace(/\s+/g, ' ')
      .slice(0, 40);
  const small = [];
  let targets = 0;
  for (const e of document.querySelectorAll('a[href], button, input, select, textarea, summary, [role=button], [role=tab], [role=slider], label[for]')) {
    const r = seen(e);
    if (!r) continue;
    // a link inside a sentence is exempt (WCAG 2.5.8, inline)
    if (e.tagName === 'A' && getComputedStyle(e).display === 'inline' && e.parentElement && e.parentElement.textContent.trim().length > e.textContent.trim().length + 20) continue;
    targets++;
    if (r.width < 43.5 || r.height < 43.5) small.push({ what: name(e), w: Math.round(r.width), h: Math.round(r.height) });
  }
  const tiny = new Map();
  const cut = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const t = n.textContent.trim();
    if (t.length < 2 || !n.parentElement) continue;
    const r = seen(n.parentElement);
    if (!r) continue;
    const fs = parseFloat(getComputedStyle(n.parentElement).fontSize);
    if (fs < 12) tiny.set(fs, (tiny.get(fs) || 0) + 1);
  }
  for (const e of document.querySelectorAll('button, a[href], input, select, [role=tab], .panel, [role=dialog]')) {
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    if (r.width < 1 || s.visibility === 'hidden' || s.display === 'none') continue;
    if (r.top < vh && r.bottom > 0 && (r.right > vw + 1 || r.left < -1) && r.left < vw && r.right > 0) cut.push(name(e));
  }
  return {
    window: [vw, vh],
    hscroll: doc.scrollWidth - vw,
    page_height: doc.scrollHeight,
    targets,
    small_targets: small.length,
    smallest: small.sort((a, b) => Math.min(a.w, a.h) - Math.min(b.w, b.h)).slice(0, 12),
    tiny_text: [...tiny.entries()].sort((a, b) => a[0] - b[0]).map(([px, n]) => `${px}px ×${n}`),
    cut_by_window: [...new Set(cut)].slice(0, 10),
  };
};

/** The box and the look of every element on the page: two builds that give the same list draw the same layout. */
const geometry = () => {
  const out = [];
  const keys = ['display', 'position', 'fontSize', 'fontWeight', 'fontFamily', 'lineHeight', 'letterSpacing', 'color', 'backgroundColor', 'backgroundImage', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'borderTopColor', 'borderRadius', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'marginTop', 'marginRight', 'marginBottom', 'marginLeft', 'opacity', 'transform', 'zIndex', 'boxShadow', 'overflowX', 'overflowY', 'visibility', 'textTransform', 'gap'];
  for (const e of document.body.querySelectorAll('*')) {
    if (e.closest('svg') && e.tagName !== 'svg') continue;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    out.push({
      el: `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}${typeof e.className === 'string' && e.className ? '.' + e.className.trim().split(/\s+/).join('.') : ''}`,
      box: [r.left, r.top, r.width, r.height].map((v) => Math.round(v * 100) / 100),
      css: keys.map((k) => s[k]).join('|'),
    });
  }
  return out;
};

async function run(sizeKey) {
  const S = SIZES[sizeKey];
  // the new headless Chromium with the real GPU: the lab and the globe are WebGL, and the software renderer of the headless shell
  // cannot keep up on a busy machine
  const browser =
    ENGINE === 'webkit' ? await webkit.launch() : await chromium.launch({ channel: 'chromium', args: ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist'] });
  const report = {};
  try {
    const ctx = await browser.newContext({
      viewport: { width: S.width, height: S.height },
      deviceScaleFactor: S.dsf,
      isMobile: S.mobile,
      hasTouch: S.mobile,
      userAgent: S.mobile
        ? S.width >= 768 || S.height >= 768
          ? 'Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
          : 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
        : undefined,
    });
    // the newcomer tour is its own subject: the screens are captured as a returning visitor sees them (--tour keeps it)
    if (!process.argv.includes('--tour')) await ctx.addInitScript(() => localStorage.setItem('spherex-tour-seen', '1'));
    const page = await ctx.newPage();
    page.setDefaultTimeout(60000);
    const dir = join(OUT, sizeKey);
    mkdirSync(dir, { recursive: true });
    const shot = async (name, full = false) => {
      await page.waitForTimeout(350);
      await page.screenshot({ path: join(dir, `${name}.png`), fullPage: full });
      report[name] = await page.evaluate(measure);
      if (GEOM && !full) writeFileSync(join(dir, `${name}.geom.json`), JSON.stringify(await page.evaluate(geometry)));
    };
    const go = async (hash) => {
      await page.goto(`${URL}/${hash}`);
      await page.waitForTimeout(300);
    };
    const tap = (loc) => (S.mobile ? loc.tap() : loc.click());
    const step = async (group, fn) => {
      if (ONLY.length && !ONLY.includes(group)) return;
      try {
        await fn();
      } catch (e) {
        report[`${group}!error`] = String(e).split('\n')[0].slice(0, 300);
        console.log(`  ${sizeKey} ${group}: ${report[`${group}!error`]}`);
      }
    };

    await step('landing', async () => {
      await go('');
      await page.getByTestId('landing').waitFor();
      await page.waitForTimeout(2500);
      await shot('01-landing');
      await shot('01-landing-full', true);
    });

    await step('globe', async () => {
      await go('#/explore');
      await page.getByTestId('sky').waitFor();
      await page.waitForTimeout(5000);
      await shot('02-globe');
      await tap(page.getByTestId('cov-toggle'));
      await page.waitForTimeout(2500);
      await shot('03-globe-coverage');
      const box = await page.getByTestId('sky').boundingBox();
      const p = [box.x + box.width / 2, box.y + box.height * 0.42];
      if (S.mobile) await page.touchscreen.tap(p[0], p[1]);
      else await page.mouse.click(p[0], p[1]);
      await page.getByTestId('cov-card').waitFor({ timeout: 8000 });
      await page.waitForTimeout(800);
      await shot('04-globe-square-card');
    });

    const labReady = () => page.getByTestId('lab-status').filter({ hasText: LOADED }).waitFor({ timeout: 420000 });
    await step('lab', async () => {
      await go(LAB);
      await labReady();
      await page.waitForTimeout(1500);
      await shot('05-lab');
      await shot('05-lab-full', true);
      await go(`${LAB}&known=1`);
      await labReady();
      await page.waitForTimeout(4000);
      await shot('06-lab-known');
      const tool = page.getByTestId('tool-mark');
      await tool.scrollIntoViewIfNeeded();
      await shot('07-lab-tools');
      await tap(tool);
      const pic = page.getByTestId('picture');
      await pic.scrollIntoViewIfNeeded();
      const b = await pic.boundingBox();
      const p = [b.x + b.width * 0.4, b.y + b.height * 0.4];
      if (S.mobile) await page.touchscreen.tap(p[0], p[1]);
      else await page.mouse.click(p[0], p[1]);
      await page.getByTestId('mark-editor').waitFor({ timeout: 8000 });
      await shot('08-lab-mark');
      await shot('08-lab-mark-full', true);
    });

    await step('task', async () => {
      await go('#/lab?ra=27.378&dec=-0.120&fov=20&task=cell');
      await page.getByTestId('task-panel').waitFor({ timeout: 30000 });
      await page.waitForTimeout(3000);
      await shot('09-lab-task');
      await shot('09-lab-task-full', true);
    });

    await step('findings', async () => {
      await go('#/findings');
      await page.locator('.fcard').first().waitFor({ timeout: 40000 });
      await page.waitForTimeout(3000);
      await shot('10-findings');
      await shot('10-findings-full', true);
      await tap(page.locator('.fcard-pic').first());
      await page.waitForTimeout(3000);
      await shot('11-finding-open');
      await shot('11-finding-open-full', true);
    });

    for (const [i, s] of [['12', 'planetx'], ['13', 'index'], ['14', 'method'], ['15', 'about']]) {
      await step(s, async () => {
        await go(`#/${s}`);
        await page.waitForTimeout(4000);
        await shot(`${i}-${s}`);
        // the whole Method page at three device pixels per pixel is too large a picture for the browser
        if (s !== 'method') await shot(`${i}-${s}-full`, true);
      });
    }

    await step('cabinet', async () => {
      await go('#/me');
      await page.getByTestId('signin').waitFor();
      await shot('16-cabinet-guest');
      const mail = page.getByLabel('E-mail address');
      await tap(mail);
      await mail.fill(EMAIL);
      report['16-email-input'] = await mail.evaluate((e) => ({ type: e.type, inputmode: e.inputMode, autocomplete: e.autocomplete, fontSize: getComputedStyle(e).fontSize }));
      await tap(page.getByRole('button', { name: 'Send me a sign-in link' }));
      await page.getByTestId('dev-link').waitFor();
      await shot('17-cabinet-link-sent');
      await tap(page.getByTestId('dev-link'));
      await page.getByTestId('who-name').waitFor();
      const inv = await page.evaluate(async () => {
        const list = await fetch('/api/me/investigations').then((r) => r.json());
        const have = (list.investigations || []).find((x) => x.title === 'Fast mover near Barnard (mobile audit)');
        if (have) return have.id;
        const d = await fetch('/api/me/investigations', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ title: 'Fast mover near Barnard (mobile audit)', finding_id: 'film:2e26f8ee6415' }),
        }).then((r) => r.json());
        return d.id;
      });
      for (const t of ['squares', 'marks', 'findings', 'investigations', 'tasks', 'history', 'notifications', 'account']) {
        const tab = page.getByTestId(`tab-${t}`);
        await tab.scrollIntoViewIfNeeded();
        await tap(tab);
        await page.getByTestId(`panel-${t}`).waitFor();
        await page.waitForTimeout(900);
        await page.evaluate(() => window.scrollTo(0, 0));
        await shot(`18-cabinet-${t}`);
      }
      await go(`#/me?tab=investigations&inv=${inv}`);
      await page.getByTestId('investigation-page').waitFor();
      await page.waitForTimeout(1500);
      await shot('19-investigation');
      await shot('19-investigation-full', true);
    });

    await ctx.close();
  } finally {
    await browser.close();
  }
  writeFileSync(join(OUT, `${sizeKey}${ONLY.length ? '-' + ONLY.join('-') : ''}.json`), JSON.stringify(report, null, 1));
  const bad = Object.entries(report)
    .filter(([k, v]) => typeof v === 'object' && !k.endsWith('-full') && v.window)
    .map(([k, v]) => `${k}: hscroll ${v.hscroll}, small ${v.small_targets}/${v.targets}${v.cut_by_window.length ? `, cut ${v.cut_by_window.length}` : ''}`);
  console.log(`${sizeKey} (${ENGINE})\n  ${bad.join('\n  ')}`);
}

for (const s of sizes) await run(s);
