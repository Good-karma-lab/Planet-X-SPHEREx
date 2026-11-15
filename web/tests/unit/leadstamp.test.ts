import { describe, expect, it } from 'vitest';
import { leadStamp, type DistantCandidate, type DistantState } from '../../src/screens/search/distant';

describe('leadStamp', () => {
  it('brings a source that stays in one place along its orbit into focus', () => {
    const n = 64;
    const V = 4;
    const bin = new Uint8Array(V * n * n);
    for (let i = 0; i < V; i++)
      for (let p = 0; p < n * n; p++) {
        const x = p % n;
        const y = Math.floor(p / n);
        const noise = ((p * 7919 + i * 104729) % 11) - 5; // fixed pseudo-noise
        const src = Math.hypot(x - 40, y - 30) < 1.5 ? 4 : 0;
        bin[i * n * n + p] = Math.round(128 + 25 * ((noise / 5) * 0.4 + src));
      }
    const ds = {
      state: 'ok',
      r: {
        slider: {
          n,
          step: 3,
          distances: [40, 60],
          offsets_px: [Array(V).fill([0, 0]), Array(V).fill([0, 0])],
          mu_max: [1, 1],
          dt: [0, 1, 2, 3],
          sg: [1, 1, 1, 1],
          t: [0, 1, 2, 3],
        },
      },
      bin,
    } as unknown as Extract<DistantState, { state: 'ok' }>;
    const c = { d: 41, mu: [0, 0], ra: 10 - ((40 - 32) * 3) / 3600, dec: 5 + ((30 - 32) * 3) / 3600, sn: 6 } as unknown as DistantCandidate;
    const st = leadStamp(ds, c, 10, 5, 12)!;
    expect(st).not.toBeNull();
    expect(st.size).toBe(25);
    expect(st.peak).toBeGreaterThan(3);
    // the brightest pixel of the stamp is in its middle
    let best = 0;
    let bi = 0;
    for (let k = 0; k < st.size * st.size; k++) if (st.rgba[k * 4]! > best) ((best = st.rgba[k * 4]!), (bi = k));
    expect(Math.abs((bi % st.size) - 12)).toBeLessThanOrEqual(2);
    expect(Math.abs(Math.floor(bi / st.size) - 12)).toBeLessThanOrEqual(2);
  });
});
