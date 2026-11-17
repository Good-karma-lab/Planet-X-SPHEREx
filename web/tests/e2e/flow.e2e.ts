// The product flow walked like a user, on the integration build (product spec §4): sign in → Findings → check a finding in the
// lab → check and submit → refused for a known star, with the reason → a finding with the credit → verdict → notification.
// Steps the lab does not have yet are `test.fixme` with the reason; nothing is faked.
import { expect, test, type Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

let page: Page;
const email = `e2e-${Date.now().toString(36)}@example.org`;
const shot = (name: string) => page.screenshot({ path: `test-results/flow/${name}.png` });

interface Find {
  id: string;
  ra: number;
  dec: number;
  scans: string[];
  data: { track?: { ra?: (number | null)[]; dec?: (number | null)[]; mjd?: (number | null)[] } };
}

test.beforeAll(async ({ browser }) => {
  page = await (await browser.newContext()).newPage();
});
test.afterAll(async () => {
  await page.context().close();
});

test('1. a guest signs in by the e-mail link (dev mode)', async () => {
  await page.goto('/#/me');
  await expect(page.getByTestId('signin')).toBeVisible();
  await page.getByLabel('E-mail', { exact: true }).fill(email);
  await page.getByRole('button', { name: 'Continue with e-mail' }).click();
  await expect(page.getByTestId('signin-status')).toContainText('We sent a link to');
  await page.getByTestId('dev-link').click();
  await expect(page.getByTestId('who-name')).toHaveText(email.split('@')[0]!);
  await shot('1-signed-in');
});

let findingId = '';

test('2. Findings: "Check this finding" opens the lab with the task', async () => {
  await page.getByTestId('nav-findings').click();
  const go = page.getByRole('link', { name: 'Check this finding' }).first();
  await expect(go).toBeVisible({ timeout: 30_000 });
  // the list is still filling in while its first cards show: a click that lands while it moves is made again
  await expect(async () => {
    if (!/#\/lab\?/.test(page.url())) await go.click({ timeout: 3000 });
    await expect(page).toHaveURL(/#\/lab\?.*task=finding/, { timeout: 3000 });
  }).toPass({ timeout: 40_000 });
  findingId = new URLSearchParams(page.url().split('?')[1]).get('finding') ?? '';
  expect(findingId).not.toBe('');
  await shot('2-lab-with-task');
});

const LOADED = /^[A-Z][a-z]{2} ’\d\d/; // the lab's status line is the date of the pass on show once the field is drawn

test('3. the lab shows the task panel "Is this a real object?" for that finding', async () => {
  await expect(page.getByTestId('task-panel')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('task-panel')).toContainText('Check a machine finding');
  await expect(page.getByTestId('task-panel')).toContainText('Is this a real object?');
  for (const b of ['task-moves', 'task-artefact', 'task-known']) await expect(page.getByTestId(b)).toBeVisible();
  await shot('3-task-panel');
});

const labels = [`e2e A ${Date.now().toString(36)}`, `e2e B ${Date.now().toString(36)}`];

test('4. the user puts marks on two passes in the lab; they appear in My marks', async () => {
  // a finding's field that nobody opened yet is built now: the lab says how far it is (minutes for a cold 20′ field)
  test.setTimeout(900_000);
  await expect(page.getByTestId('lab-status')).toHaveText(LOADED, { timeout: 780_000 });
  const picture = page.getByTestId('picture');
  const box = (await picture.boundingBox())!;
  const dates: string[] = [];
  // the passes play one after another (Blink): an arrow key stops on one pass, so that a mark belongs to the pass the user sees
  const stay = async () => {
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await page.keyboard.press('ArrowRight');
    let seen = '';
    await expect(async () => {
      const now = (await page.getByTestId('lab-status').innerText()).trim();
      const same = now === seen && LOADED.test(now);
      seen = now;
      expect(same).toBe(true);
    }).toPass({ timeout: 30_000, intervals: [1500] });
    return seen;
  };
  for (const [i, at] of [[0, [0.3, 0.3]], [1, [0.7, 0.68]]] as const) {
    dates.push(await stay()); // the first pass to stay on, then the next one
    if (i === 1) expect(dates[1]).not.toBe(dates[0]);
    const tool = page.getByTestId('tool-mark');
    await expect(tool).toBeEnabled({ timeout: 20_000 });
    if ((await tool.getAttribute('aria-pressed')) !== 'true') await tool.click();
    await page.mouse.click(box.x + box.width * at[0], box.y + box.height * at[1]);
    await expect(page.getByTestId('mark-editor')).toBeVisible();
    await page.getByTestId('mark-label').fill(labels[i]!);
    await expect(page.getByTestId('marks-list')).toContainText(labels[i]!);
  }
  expect(dates[0]).not.toBe(dates[1]);
  await expect(page.getByTestId('marks-list').locator('.ft-mark')).toHaveCount(2);
  await shot('4-two-marks-in-the-lab');
  // the account has them: the cabinet shows both, each with the pass it was put on
  await page.getByTestId('nav-me').click();
  await page.getByTestId('tab-marks').click();
  for (const l of labels) await expect(page.getByTestId('panel-marks')).toContainText(l);
  const passes = await page.getByTestId('mark-row').locator('.mono-s').allInnerTexts();
  expect(new Set(passes.map((t) => /pass (\d+)/.exec(t)?.[1])).size).toBe(2);
  await shot('4-my-marks');
});

test('5. Check and submit on a known star: the checks run and the submit is refused, with the reason', async () => {
  // The marks of this step are written through the account API at the measured positions of a real known star (Barnard's Star, in
  // the passes the machine measured it in): hitting a star to the arcsecond by clicking is not what this step is about.
  const made = await page.evaluate(async () => {
    const j = (p: string, b?: unknown) =>
      fetch(p, b ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) } : undefined).then((r) => r.json());
    const finds = ((await j('/api/v1/findings?status=known&kind=mover&limit=200')) as { finds: Find[] }).finds;
    const star = finds.find((f) => /Barnard/.test((f as unknown as { place: string }).place) && (f.data.track?.ra ?? []).filter((x) => x != null).length >= 2);
    if (!star) return null;
    const t = star.data.track!;
    // the key the lab gives a mark: the centre and size of the field on show ("ra,dec,fov"), here the 20′ field at the star
    const key = `${star.ra.toFixed(4)},${star.dec >= 0 ? '+' : ''}${star.dec.toFixed(4)},20`;
    const ids: string[] = [];
    for (let i = 0; i < t.ra!.length; i++) {
      if (t.ra![i] == null || t.dec![i] == null) continue;
      const id = `e2e-${Date.now().toString(36)}-${i}`;
      await j('/api/me/marks', { id, field_key: key, cell_id: null, ra: t.ra![i], dec: t.dec![i], pass: i, label: `Barnard pass ${i + 1}`, level: 'suspicious', note: '', created: new Date().toISOString() });
      ids.push(id);
    }
    const inv = (await j('/api/me/investigations', { title: 'A known star (end-to-end)', mark_ids: ids })) as { id: string };
    return { inv: inv.id, marks: ids.length, star: star.id };
  });
  expect(made, 'a known mover near Barnard\'s Star with a measured path is in the findings').not.toBeNull();
  expect(made!.marks).toBeGreaterThanOrEqual(2);

  await page.goto(`/#/me?inv=${made!.inv}`);
  await expect(page.getByTestId('investigation-page')).toBeVisible();
  await page.getByTestId('check-submit').click();
  // "Check and submit" is said once; a quick check that fails ends the job early and the page says which
  await expect(page.getByTestId('checks-status')).toHaveText(/^Not submitted · (gaia|simbad|edge bright): fail$/, { timeout: 300_000 });
  await expect(page.getByTestId('checks').getByText(/^gaia: fail$/i)).toBeVisible();
  await expect(page.getByTestId('investigation-page')).toContainText(/gaia: Gaia DR3 \d+ .*a known object/);
  await expect(page.getByTestId('investigation-page')).toContainText(/simbad: SIMBAD Barnard's star/);
  await expect(page.getByTestId('check-submit')).toHaveText('Check again');
  await shot('5-refused-known-star');
  // the user is told the same in the notifications, with the reason
  await page.getByTestId('tab-notifications').click();
  await expect(page.getByTestId('panel-notifications')).toContainText(/“A known star \(end-to-end\)” was not submitted\. .*a known object/);
  // and the API refuses the same
  const code = await page.evaluate(async (inv) => {
    const i = ((await fetch('/api/me/investigations').then((r) => r.json())) as { investigations: { id: string; checks_job: string }[] }).investigations.find((x) => x.id === inv)!;
    return fetch('/api/findings/submit', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ investigation_id: inv, checks_job: i.checks_job }) }).then((r) => r.status);
  }, made!.inv);
  expect(code).toBe(409);
});

const LEAD = 'film:2e26f8ee6415'; // the one real unexplained lead with a path: it moves inside one pass (five dated visits)
let submitted = false;

test('6. a case that passes: the fast-mover lead is checked, commented and submitted; it is in My findings and in Findings', async () => {
  test.setTimeout(1_800_000);
  // The investigation of the lead is opened through the account API (in the lab it is "Save to investigation" on the finding).
  const inv = await page.evaluate(
    (lead) => fetch('/api/me/investigations', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'Fast mover near Barnard (end-to-end)', finding_id: lead }) }).then((r) => r.json()).then((d: { id: string }) => d.id),
    LEAD,
  );
  await page.goto(`/#/me?inv=${inv}`);
  // said once, with the first comment given up front; then the user leaves the page
  await page.getByTestId('declare-note').fill('Not one straight line: the body is 0.4 au away and its path curves over 169 hours; the track search fits all five visits.');
  await page.getByTestId('check-submit').click();
  await expect(page.getByTestId('checks-status')).toHaveText(/^(Submit declared · .*you may leave|Every check has answered · one comment needed)/, { timeout: 60_000 });
  await shot('6-declared');
  await page.goto('/#/me');
  await expect(page.getByTestId('investigation').filter({ hasText: 'Fast mover near Barnard (end-to-end)' })).toBeVisible();
  await shot('6-tasks-progress');
  // JPL is asked for each of the five visits (minutes): the server goes on without the page and says when a comment is needed
  await expect(async () => {
    await page.keyboard.press('Escape'); // the bell's panel closed, then opened again: it reads the notifications anew
    await page.getByTestId('tab-notifications').click();
    await expect(page.getByTestId('panel-notifications')).toContainText('“Fast mover near Barnard (end-to-end)”: every check has answered. One comment is needed', { timeout: 5000 });
  }).toPass({ timeout: 1_500_000, intervals: [15_000] });
  await page.goto(`/#/me?inv=${inv}`);
  await expect(page.getByTestId('checks-status')).toHaveText(/one comment needed for the warnings/);
  await expect(page.getByTestId('checks').getByText(/^two passes: pass$/i)).toBeVisible();
  // the second comment is all it waited for: the finding is submitted with no further click
  await page.getByPlaceholder('Add a note').fill('The Gaia star is 2.7″ from the first visit only; the other four visits are far from it.');
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByTestId('verdict')).toContainText('Submitted. Waiting for the verdict.');
  submitted = true;
  await shot('6-submitted');
  await page.goto('/#/me?work=findings');
  await expect(page.getByTestId('panel-findings')).toContainText('Fast mover near Barnard (end-to-end)');
  await expect(page.getByTestId('panel-findings')).toContainText(/submitted/i);
  // Findings: the card of the lead carries the user's name
  await page.getByTestId('nav-findings').click();
  const name = email.split('@')[0]!;
  await expect(page.locator('.fcard-credit', { hasText: name }).first()).toBeVisible({ timeout: 60_000 });
  await shot('6-findings-credit');
});

test('7. the verdict tool changes the status and the Cabinet tab shows the unread badge', async () => {
  test.skip(!submitted, 'step 6 did not submit');
  test.skip(!process.env.SKY_FLOW_PRODUCT_DB, 'SKY_FLOW_PRODUCT_DB (the product database of the build under test) is not set');
  const { execSync } = await import('node:child_process');
  await page.goto('/#/me?tab=notifications');
  await page.getByTestId('panel-notifications').waitFor();
  await page.evaluate(() => fetch('/api/me/notifications/read', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }));
  const out = execSync(`${process.env.UV ?? 'uv'} run --project .. python -m service.accounts.verdict ${LEAD} candidate`, { cwd: '..', env: { ...process.env, SKY_PRODUCT_DB: process.env.SKY_FLOW_PRODUCT_DB } }).toString();
  expect(out).toContain('candidate');
  await page.keyboard.press('Escape');
  await page.getByTestId('tab-notifications').click();
  await expect(page.getByTestId('panel-notifications')).toContainText('the status is now “candidate”');
  await expect(page.getByTestId('nav-unread')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByTestId('tab-findings').click();
  await expect(page.getByTestId('panel-findings')).toContainText(/candidate/i);
  await shot('7-verdict');
});

test('8. the cabinet keeps the work: the investigation, its marks with pictures, the checks notification', async () => {
  await page.goto('/#/me?tab=marks');
  await expect(page.getByTestId('mark-row').first()).toBeVisible();
  expect(await page.getByTestId('mark-row').count()).toBeGreaterThanOrEqual(2);
  // the notification comes when the whole job has finished (JPL may still be answering)
  const finished = await page.evaluate(async () => {
    const i = ((await fetch('/api/me/investigations').then((r) => r.json())) as { investigations: { checks: { state: string } | null }[] }).investigations[0];
    return i?.checks?.state === 'done';
  });
  await page.getByTestId('tab-notifications').click();
  if (finished) {
    await expect(page.getByTestId('panel-notifications')).toContainText('“A known star (end-to-end)” was not submitted');
    await expect(page.getByTestId('nav-unread')).toBeVisible();
  } else {
    test.info().annotations.push({ type: 'note', description: 'the checks job was still running (JPL): the "checks finished" notification was not asserted' });
  }
  await page.keyboard.press('Escape');
  await page.getByTestId('tab-history').click();
  await expect(page.getByTestId('panel-history')).toBeVisible();
  await shot('8-cabinet');
});

test('9. the user signs out; the site still works for a guest', async () => {
  await page.goto('/#/me');
  await page.getByTestId('account-menu').click();
  await page.getByTestId('signout').click();
  await expect(page.getByTestId('signin')).toBeVisible();
  expect(await page.evaluate(() => fetch('/api/me/marks').then((r) => r.status))).toBe(401);
  await page.getByTestId('nav-findings').click();
  await expect(page.getByRole('link', { name: 'Check this finding' }).first()).toBeVisible({ timeout: 30_000 });
});
