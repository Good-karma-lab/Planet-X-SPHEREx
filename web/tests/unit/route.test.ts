import { describe, expect, it } from 'vitest';
import { formatHash, parseHash } from '../../src/lib/route';

describe('hash routes', () => {
  it('reads a screen and its parameters', () => {
    expect(parseHash('#/lab?ra=269.4479&dec=4.7687&fov=5')).toEqual({
      screen: 'lab',
      params: { ra: '269.4479', dec: '4.7687', fov: '5' },
    });
  });
  it('falls back to the globe for an empty or unknown screen', () => {
    expect(parseHash('').screen).toBe('globe');
    expect(parseHash('#/nowhere').screen).toBe('globe');
  });
  it('round-trips', () => {
    const r = { screen: 'lab' as const, params: { ra: '331.077', dec: '-56.794', fov: '5' } };
    expect(parseHash(formatHash(r))).toEqual(r);
  });
});
