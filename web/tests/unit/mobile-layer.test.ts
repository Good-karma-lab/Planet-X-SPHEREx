// The responsive layer's own logic (ui/Sheet.tsx, ui/touch.ts): the heights a bottom sheet rests at, where it settles when let go,
// the wheel step a two-finger pinch turns into, and where the magnifier for marks stands.
import { describe, expect, it } from 'vitest';
import { detentHeights, settle } from '../../src/ui/Sheet';
import { LOUPE, loupePlace, wheelFor } from '../../src/ui/touch';

describe('bottom sheet: the three heights', () => {
  it('peek is the header, half is half the window, full is what the style sheet allows', () => {
    expect(detentHeights(700, 120, 844)).toEqual({ peek: 120, half: 422, full: 700 });
  });
  it('a header taller than half the window lifts half up to it; nothing passes full', () => {
    const h = detentHeights(300, 260, 400);
    expect(h.peek).toBe(260);
    expect(h.half).toBe(260);
    expect(h.full).toBe(300);
  });
  it('a header taller than the sheet may be is cut to it', () => {
    expect(detentHeights(200, 500, 800).peek).toBe(200);
  });
});

describe('bottom sheet: where it comes to rest', () => {
  const h = { peek: 100, half: 400, full: 700 };
  it('a slow release goes to the nearest height', () => {
    expect(settle(180, 0, h)).toBe('peek');
    expect(settle(300, 0, h)).toBe('half');
    expect(settle(600, 0, h)).toBe('full');
  });
  it('a flick up from a low place reaches the next height, a flick down comes back', () => {
    expect(settle(180, 1.2, h)).toBe('half');
    expect(settle(380, -1.5, h)).toBe('peek');
  });
});

describe('two-finger zoom of the globe', () => {
  it('spreading the fingers gives a negative wheel step (zoom in), closing them a positive one', () => {
    expect(wheelFor(1.2)).toBeLessThan(0);
    expect(wheelFor(0.8)).toBeGreaterThan(0);
    expect(wheelFor(1)).toBeCloseTo(0);
  });
  it('the step turns back into the same factor by the globe\'s law, zoom × exp(−step · 0.0022)', () => {
    for (const k of [0.6, 0.9, 1.1, 1.7]) expect(Math.exp(-wheelFor(k) * 0.0022)).toBeCloseTo(k, 6);
  });
  it('one step never asks for more than the globe takes in one wheel event (×0.5 … ×2)', () => {
    expect(Math.exp(-wheelFor(5) * 0.0022)).toBeCloseTo(2, 6);
    expect(Math.exp(-wheelFor(0.1) * 0.0022)).toBeCloseTo(0.5, 6);
  });
});

describe('the magnifier for marks', () => {
  it('stands above the finger, centred on it', () => {
    const p = loupePlace(200, 400, 390);
    expect(p.left).toBe(200 - LOUPE.size / 2);
    expect(p.top + LOUPE.size).toBeLessThan(400 - 10);
  });
  it('near the top of the window it moves beside the finger, to the side that has room, and never covers the point', () => {
    const left = loupePlace(60, 30, 390);
    expect(left.top).toBeGreaterThanOrEqual(4);
    expect(left.left).toBeGreaterThanOrEqual(60 + 20);
    const right = loupePlace(340, 30, 390);
    expect(right.left + LOUPE.size).toBeLessThanOrEqual(340 - 20);
  });
  it('stays inside the window at its edges', () => {
    expect(loupePlace(5, 400, 390).left).toBe(4);
    expect(loupePlace(388, 400, 390).left).toBe(390 - LOUPE.size - 4);
  });
});
