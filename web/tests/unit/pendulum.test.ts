import { describe, expect, it } from 'vitest';
import { FADE_MS, HOLD_MS, frameAt, order } from '../../src/viewer/pendulum';
import { stretch } from '../../src/viewer/law';

describe('pendulum', () => {
  it('goes forth and back without a return jump', () => {
    expect(order(3)).toEqual([0, 1, 2, 1]);
    expect(order(4)).toEqual([0, 1, 2, 3, 2, 1]);
    expect(order(1)).toEqual([0]);
  });
  it('holds, then crossfades with smoothstep', () => {
    expect(frameAt(0, 3)).toEqual({ a: 0, b: 0, t: 0, step: 0 });
    const mid = frameAt(HOLD_MS + FADE_MS / 2, 3);
    expect(mid.a).toBe(0);
    expect(mid.b).toBe(1);
    expect(mid.t).toBeCloseTo(0.5);
    expect(frameAt(3 * (HOLD_MS + FADE_MS), 3).a).toBe(1); // 0,1,2,1 → the fourth slot shows pass index 1
  });
});

describe('display law', () => {
  it('is 0 at the floor, 1 at the top, monotonic', () => {
    expect(stretch(-0.05)).toBe(0);
    expect(stretch(400)).toBeCloseTo(1);
    expect(stretch(1)).toBeGreaterThan(stretch(0.5));
  });
});
