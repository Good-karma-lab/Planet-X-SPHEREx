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
  await page.getByLabel('E-mail address').fill(email);
  await page.getByRole('button', { name: 'Send me a sign-in link' }).click();
  await expect(page.getByTestId('signin-status')).toContainText('Link sent');
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

test.fixme('3. the lab shows the task panel "Is this a real object?" for that finding', async () => {
  // not in the integration build: the lab stream's task panel (branch flow-lab, web/src/screens/lab/task.tsx) is not merged
  await expect(page.getByTestId('task-panel')).toBeVisible();
});

test.fixme('4. the user puts marks on two passes in the lab', async () => {
  // not in the integration build: marks in the lab (branch flow-lab, web/src/screens/lab/flow.tsx, lib/marks.ts) are not merged
  await page.getByTestId('tool-mark').click();
});

test('5. Check and submit on a known star: the checks run and the submit is refused, with the reason', async () => {
  // The marks of this step are written through the account API (what the lab's marks do once merged), at the measured positions
  // of a real known star: Barnard's Star, in the passes the machine measured it in.
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

  await page.goto(`/#/me?tab=investigations&inv=${made!.inv}`);
  await expect(page.getByTestId('investigation-page')).toBeVisible();
  await page.getByTestId('check-submit').click();
  // the checks answer one by one (JPL and NEOWISE are slow); the reason of the refusal is on the page as soon as Gaia has answered
  await expect(page.getByTestId('checks').getByText(/^gaia: fail$/i)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId('investigation-page')).toContainText(/gaia: Gaia DR3 \d+ .*a known object/);
  await expect(page.getByTestId('investigation-page')).toContainText(/simbad: SIMBAD Barnard's star/);
  // no way to submit: while the last checks run the button is off, after them it is "Check again"
  const button = page.getByTestId('check-submit');
  if ((await button.innerText()) === 'Submit the finding') await expect(button).toBeDisabled();
  else await expect(button).toHaveText('Check again');
  await shot('5-refused-known-star');
  // and the API refuses the same
  const code = await page.evaluate(async (inv) => {
    const i = ((await fetch('/api/me/investigations').then((r) => r.json())) as { investigations: { id: string; checks_job: string }[] }).investigations.find((x) => x.id === inv)!;
    return fetch('/api/findings/submit', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ investigation_id: inv, checks_job: i.checks_job }) }).then((r) => r.status);
  }, made!.inv);
  expect(code).toBe(409);
});

test.fixme('6. a case that passes: the finding appears in Findings with the credit, and in My findings', async () => {
  // no real case passes the real checks today: no unknown object with marks in two passes exists in the searched fields (the one
  // unexplained track, film:2e26f8ee6415, lies inside one pass and fails two_passes). The submit itself is covered against a fake
  // checks job in tests/fake_checks_site.py and tests/test_accounts.py.
});

test.fixme('7. the verdict tool changes the status and the Cabinet tab shows the unread badge', async () => {
  // needs a finding the user is credited on: a submitted one (step 6) or a machine finding closed in the lab's task panel (step 3)
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
    await expect(page.getByTestId('panel-notifications')).toContainText('The checks of “A known star (end-to-end)” have finished');
    await expect(page.getByTestId('nav-unread')).toBeVisible();
  } else {
    test.info().annotations.push({ type: 'note', description: 'the checks job was still running (JPL): the "checks finished" notification was not asserted' });
  }
  await page.getByTestId('tab-history').click();
  await expect(page.getByTestId('panel-history')).toBeVisible();
  await shot('8-cabinet');
});

test('9. the user signs out; the site still works for a guest', async () => {
  await page.goto('/#/me');
  await page.getByTestId('signout').click();
  await expect(page.getByTestId('signin')).toBeVisible();
  expect(await page.evaluate(() => fetch('/api/me/marks').then((r) => r.status))).toBe(401);
  await page.getByTestId('nav-findings').click();
  await expect(page.getByRole('link', { name: 'Check this finding' }).first()).toBeVisible({ timeout: 30_000 });
});
