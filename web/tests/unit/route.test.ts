import { describe, expect, it } from 'vitest';
import { formatHash, parseHash } from '../../src/lib/route';

describe('hash routes', () => {
  it('reads a screen and its parameters', () => {
    expect(parseHash('#/lab?ra=269.4479&dec=4.7687&fov=5')).toEqual({
      screen: 'lab',
      params: { ra: '269.4479', dec: '4.7687', fov: '5' },
    });
  });
  it('opens the landing page for an empty or unknown screen; #/explore (and old #/globe links) is the globe', () => {
    expect(parseHash('').screen).toBe('landing');
    expect(parseHash('#/nowhere').screen).toBe('landing');
    expect(parseHash('#/explore').screen).toBe('globe');
    expect(parseHash('#/globe').screen).toBe('globe');
    expect(formatHash({ screen: 'globe', params: {} })).toBe('#/explore');
  });
  it('round-trips', () => {
    const r = { screen: 'lab' as const, params: { ra: '331.077', dec: '-56.794', fov: '5' } };
    expect(parseHash(formatHash(r))).toEqual(r);
  });
});
