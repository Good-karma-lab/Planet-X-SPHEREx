import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { takeTask as checkThisFinding, type CardFind } from '../../src/screens/findings/cards';
import { taskBody, taskOfParams, takeTask as labTakeTask, taskParams } from '../../src/screens/lab/task';
import { parseHash } from '../../src/lib/route';

// The service as a guest sees it: every GET /api/tasks/next makes a new guest task (an acc_tasks row).
let made: string[] = [];
function service(url: string): Response {
  if (url.startsWith('/api/tasks/next')) {
    const q = new URLSearchParams(url.split('?')[1] ?? '');
    const id = `t_${made.length + 1}`;
    made.push(id);
    const finding = q.get('finding_id');
    const cell = q.get('cell_id');
    const task = finding
      ? { id, kind: 'finding', cell_id: null, finding_id: finding, field_key: null, ra: 10, dec: -5, fov: 10, state: 'open', result: null, mark_ids: [], created: '', done_at: null }
      : { id, kind: 'cell', cell_id: cell ?? 'c7', quarter: 'NE', finding_id: null, field_key: null, ra: 11, dec: -6, state: 'open', result: null, mark_ids: [], created: '', done_at: null };
    return new Response(JSON.stringify({ task }), { status: 200 });
  }
  if (url.startsWith('/api/me/tasks')) return new Response('{"detail":"sign in"}', { status: 401 });
  return new Response('{}', { status: 404 });
}

const store = new Map<string, string>();
const card: CardFind = { id: 'anomaly:ab12', kind: 'anomaly', place: '', fov: 10, ra: 10, dec: -5 } as CardFind;

beforeEach(() => {
  made = [];
  store.clear();
  vi.stubGlobal('fetch', vi.fn(async (u: string) => service(String(u))));
  vi.stubGlobal('location', { hash: '' });
  vi.stubGlobal('sessionStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('one "Check this finding" is one task', () => {
  it('Findings takes the task, the lab opens that same task and asks for no other', async () => {
    await checkThisFinding(card);
    expect(made).toEqual(['t_1']);
    const { screen, params } = parseHash(location.hash);
    expect(screen).toBe('lab');
    const task = taskOfParams(params)!;
    expect(task.id).toBe('t_1');
    const r = await taskBody(task, null);
    expect(r && 'body' in r ? r.body.id : null).toBe('t_1');
    expect(made).toEqual(['t_1']); // exactly one task for the click
  });

  it('a reload of the lab in the same tab keeps that task too', async () => {
    await checkThisFinding(card);
    const task = taskOfParams(parseHash(location.hash).params)!;
    vi.resetModules(); // a new page: the memory copy is gone, the tab's sessionStorage stays
    const fresh = await import('../../src/screens/lab/task');
    const r = await fresh.taskBody(task, null);
    expect(r && 'body' in r ? r.body.id : null).toBe('t_1');
    expect(made).toEqual(['t_1']);
  });

  it('the lab named by the kind only still takes one task itself', async () => {
    const task = taskOfParams({ task: 'finding', finding: 'anomaly:ab12' })!;
    const r = await taskBody(task, null);
    expect(r && 'body' in r ? r.body.id : null).toBe('t_1');
    expect(made).toEqual(['t_1']);
  });

  it("the lab's next task and a square's quarter open the task taken, not a second one", async () => {
    const next = await labTakeTask('cell', 'c7');
    expect(next && next !== 'failed' ? next.id : null).toBe('t_1');
    const params = taskParams(next as Exclude<typeof next, 'failed' | null>)!;
    const r = await taskBody(taskOfParams(params)!, null);
    expect(r && 'body' in r ? r.body.id : null).toBe('t_1');
    expect(made).toEqual(['t_1']);
  });
});

describe('a tab does not ask for a find it was given', () => {
  it('names the finds it had when it asks for any find, never for a square or one find', async () => {
    const asked: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => (asked.push(String(u)), service(String(u).replace(/kind=red(?=&|$)/, "kind=red&finding_id=red:aa")))));
    await labTakeTask('red');
    await labTakeTask('red');
    await labTakeTask('cell');
    await labTakeTask('finding', 'red:bb');
    const skips = asked.map((u) => new URLSearchParams(u.split('?')[1]).get('skip'));
    expect(skips[1]).toContain('red:aa');
    expect(skips.slice(2)).toEqual([null, null]);
  });
});
