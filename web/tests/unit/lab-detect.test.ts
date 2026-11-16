import { describe, expect, it } from 'vitest';
import { Buckets, peaksOf } from '../../src/screens/lab/detect';

describe('sources of a pass picture', () => {
  it('finds one peak per source above five sigma, a flat top once, nothing at the edge', () => {
    const n = 40;
    const v = new Float32Array(n * n).fill(1);
    v[10 * n + 10] = 30; // a source
    v[20 * n + 25] = v[20 * n + 26] = 50; // a saturated, flat top
    v[30 * n + 30] = 1.4; // noise (4σ)
    v[1 * n + 1] = 90; // at the edge
    const p = peaksOf(v, n, 1, 0.1);
    expect(p.map((q) => [q.x, q.y])).toEqual([
      [10.5, 10.5],
      [25.5, 20.5],
    ]);
    expect(p[0]!.snr).toBeCloseTo(290, 0);
  });
  it('finds the nearest point within a radius', () => {
    const b = new Buckets([{ x: 5, y: 5 }, { x: 9, y: 5 }, { x: 50, y: 50 }], 4);
    expect(b.near(8, 5, 2)).toEqual({ x: 9, y: 5 });
    expect(b.near(30, 30, 3)).toBeNull();
  });
});
