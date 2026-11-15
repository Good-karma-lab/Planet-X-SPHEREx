import { describe, expect, it } from 'vitest';
import { equiLevelFor, equiOf, registerEqui } from '../../src/lib/equi';

describe('equirect layers', () => {
  const s = { id: 't', label: 'test', size: 512, min: 2, max: 7, url: '/x/{l}/{x}_{y}.webp', gain: 1, black: 0 };
  it('registers a layer by id', () => {
    registerEqui(s);
    expect(equiOf('t')?.max).toBe(7);
    expect(equiOf('nope')).toBeUndefined();
  });
  it('picks the level whose pixels match the globe radius, within the baked levels', () => {
    // one tile pixel per screen pixel: 512·2^l px round the equator = 2πR
    expect(equiLevelFor(s, 450)).toBe(3);
    expect(equiLevelFor(s, 3500)).toBe(6);
    expect(equiLevelFor(s, 1)).toBe(2);
    expect(equiLevelFor(s, 1e9)).toBe(7);
  });
});
