import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { h, render } from 'preact';
import { handTask, type Task } from '../../src/lib/account';
import { useLabTask, type TaskArgs } from '../../src/screens/lab/task';

// 27 Nov 2026, 86.4 (exploratory report, Н1): after a red source's "Old picture next to new" (its step shows the old surveys, arch=1)
// "Next" opened the next task (a Planet X peak) on a 1993 plate without its two sums. On the next task's first render the panel still
// held the last task's body and answers, so the lab set up the last task's step ("old_new": the archive on) for the new task. A task's
// step must be its own from its first render.

beforeEach(() => {
  vi.useFakeTimers();
  (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame = (f: () => void) => setTimeout(f, 0);
  globalThis.fetch = vi.fn(async () => new Response('{}', { status: 200 })) as unknown as typeof fetch;
});
afterEach(() => vi.useRealTimers());

const page = () =>
  ({ childNodes: [], firstChild: null, ownerDocument: null, nodeType: 1, insertBefore() {}, appendChild() {}, removeChild() {} }) as unknown as Element;

const step = (id: string, show: string) => ({ id, question: `${id}?`, show, answers: [{ id: 'not_sure', label: 'Not sure', result: 'cant_tell' }] });
const task = (id: string, finding: string, first: string): Task =>
  ({ id, kind: 'finding', finding_id: finding, ra: 10, dec: 5, series: { id: `s_${id}`, steps: [step('q1', first)] } }) as unknown as Task;

test("the next task never opens on the last task's step (the old surveys of a red source on a Planet X peak)", async () => {
  handTask(task('t_red', 'red:953aa6260b24', 'old_new'));
  handTask(task('t_peak', 'peak:60c0a10726a3', 'sums'));
  const seen: [string, string | null][] = [];
  const args = (id: string, finding: string) =>
    ({ params: { task: id, kind: 'finding', finding }, open: () => {}, ra: 10, dec: 5, fieldKey: null, marksHere: [], pass: 0, passesTotal: 3 }) as unknown as TaskArgs;
  function Lab(p: { id: string; finding: string }) {
    const v = useLabTask(args(p.id, p.finding));
    seen.push([p.id, v.show]);
    return null;
  }
  const root = page();
  render(h(Lab, { id: 't_red', finding: 'red:953aa6260b24' }), root);
  await vi.advanceTimersByTimeAsync(300);
  expect(seen.some(([id, show]) => id === 't_red' && show === 'old_new')).toBe(true); // the red source's own step
  render(h(Lab, { id: 't_peak', finding: 'peak:60c0a10726a3' }), root); // "Next"
  await vi.advanceTimersByTimeAsync(300);
  const peak = seen.filter(([id]) => id === 't_peak').map(([, show]) => show);
  expect(peak).not.toContain('old_new');
  expect(peak.at(-1)).toBe('sums');
  render(null, root);
});
