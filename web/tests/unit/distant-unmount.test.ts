import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { h, render } from 'preact';
import { useDistant } from '../../src/screens/search/distant';

// 26 Nov 2026 (exploratory report, П1): the Lab's Planet X search kept asking /api/v1/distant every 5 s from About and the Cabinet
// until the job ended, so the service never dropped it. Leaving the page (unmounting) must stop the asking.

let asked: string[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  asked = [];
  // preact runs effects after paint: without a DOM, after a timer
  (globalThis as { requestAnimationFrame?: unknown }).requestAnimationFrame = (f: () => void) => setTimeout(f, 0);
  globalThis.fetch = vi.fn(async (url: string) => {
    asked.push(String(url));
    const body = String(url).includes('/health')
      ? { product_versions: { distant: '1' } }
      : { state: 'running', phase: 'exposure 131 of 174' };
    return new Response(JSON.stringify(body), { status: 200 });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.useRealTimers();
});

// a node stand-in for the page: preact needs no more of it for a component that draws nothing
const page = () =>
  ({
    childNodes: [],
    firstChild: null,
    ownerDocument: null,
    nodeType: 1,
    insertBefore() {},
    appendChild() {},
    removeChild() {},
  }) as unknown as Element;

const distantAsks = () => asked.filter((u) => u.includes('/api/v1/distant')).length;

async function mounted(aim: { ra: number; dec: number; fov: number }) {
  let api: ReturnType<typeof useDistant> | null = null;
  function Lab(p: { aim: typeof aim }) {
    api = useDistant(p.aim);
    return null;
  }
  const root = page();
  render(h(Lab, { aim }), root);
  await vi.advanceTimersByTimeAsync(300); // the effects run after paint (preact: at most 100 ms after)
  return { root, start: () => void api!.start(null, true), rerender: (a: typeof aim) => render(h(Lab, { aim: a }), root) };
}

test('leaving the Lab stops asking about its background Planet X search', async () => {
  const lab = await mounted({ ra: 44.81778, dec: 4.08893, fov: 5 });
  lab.start();
  await vi.advanceTimersByTimeAsync(11_000); // asked at once, then every 5 s
  const before = distantAsks();
  expect(before).toBeGreaterThanOrEqual(2);
  render(null, lab.root); // to About
  await vi.advanceTimersByTimeAsync(60_000);
  expect(distantAsks()).toBe(before); // was 12 more before the fix
});

test('another field stops asking about the one before', async () => {
  const lab = await mounted({ ra: 44.81778, dec: 4.08893, fov: 5 });
  lab.start();
  await vi.advanceTimersByTimeAsync(6_000);
  lab.rerender({ ra: 43.198979, dec: 9.271585, fov: 5 });
  await vi.advanceTimersByTimeAsync(30_000);
  expect(asked.filter((u) => u.includes('ra=44.81778')).length).toBeLessThanOrEqual(2);
});
