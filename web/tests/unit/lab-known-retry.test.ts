import { afterEach, describe, expect, it, vi } from 'vitest';
import { followKnown, giveUp, knownStatus, parseKnown, type KnownState } from '../../src/screens/lab/known';

// A /api/known answer whose catalogues settled: Gaia answered, SIMBAD and JPL did not, CatWISE did not, AllWISE is still asked
const SETTLED = {
  objects: [],
  answered: { gaia: true, simbad: false, jpl: false },
  status: {
    gaia: { state: 'ok' },
    simbad: { state: 'unavailable' },
    jpl: { state: 'unavailable' },
    match: { catwise: { state: 'unavailable' } },
  },
};
const ASKING = { ...SETTLED, status: { ...SETTLED.status, jpl: { state: 'pending' }, match: { catwise: { state: 'pending' } } } };

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Known objects: catalogues that do not answer are asked again on the retry schedule, with its countdown', () => {
  it('ends the lookup on a settled answer (the retry asks again, the lookup does not)', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(SETTLED), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const seen: KnownState[] = [];
    followKnown('/api/known?x', (s) => seen.push(s));
    await vi.waitFor(() => expect(seen.at(-1)?.state).toBe('ok'));
    await new Promise((r) => setTimeout(r, 1700)); // longer than the lookup's own pause between asks
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('after waiting long enough, a catalogue still being asked counts as not answering', () => {
    const r = giveUp(parseKnown(ASKING));
    expect(r.pending).toEqual([]);
    expect(r.deepPending).toEqual([]);
    expect(r.deepSilent.length).toBeGreaterThan(0);
    expect(r.answered.jpl).toBe(false);
  });

  it('says who did not answer and when the next try is', () => {
    const k: KnownState = { state: 'ok', ...parseKnown(SETTLED) } as KnownState;
    const line = knownStatus(k, { known: 3, noMatch: 1 }, 15);
    expect(line).toMatch(/SIMBAD/);
    expect(line).toMatch(/JPL/);
    expect(line).toMatch(/: no answer · retry 15 s/);
    expect(knownStatus(k, { known: 3, noMatch: 1 }, null)).not.toMatch(/retry/);
  });
});
