import { describe, expect, it } from 'vitest';
import { formatHash, parseHash } from '../../src/lib/route';

describe('hash routes', () => {
  it('reads a screen and its parameters', () => {
    expect(parseHash('#/lab?ra=269.4479&dec=4.7687&fov=5')).toEqual({
      screen: 'lab',
      params: { ra: '269.4479', dec: '4.7687', fov: '5' },
    });
  });
  it('opens the landing page for an empty address and says so for an unknown one; #/explore (and old #/globe links) is the globe', () => {
    expect(parseHash('').screen).toBe('landing');
    expect(parseHash('#/').screen).toBe('landing');
    expect(parseHash('#/nowhere')).toEqual({ screen: 'missing', params: { path: 'nowhere' } });
    expect(parseHash('#/missing').screen).toBe('missing');
    expect(parseHash('#/explore').screen).toBe('globe');
    expect(parseHash('#/globe').screen).toBe('globe');
    expect(formatHash({ screen: 'globe', params: {} })).toBe('#/explore');
  });
  it('takes the other names of a screen: #/cabinet, #/planet-x, #/leaderboard', () => {
    expect(parseHash('#/cabinet').screen).toBe('me');
    expect(parseHash('#/Cabinet?work=marks')).toEqual({ screen: 'me', params: { work: 'marks' } });
    expect(parseHash('#/planet-x').screen).toBe('planetx');
    expect(parseHash('#/leaderboard').screen).toBe('leaders');
  });
  it('round-trips', () => {
    const r = { screen: 'lab' as const, params: { ra: '331.077', dec: '-56.794', fov: '5' } };
    expect(parseHash(formatHash(r))).toEqual(r);
  });
  it("opens a finding's own page at #/findings/<id> (the id's case kept) and writes it back the same way", () => {
    expect(parseHash('#/findings/mover:AbC123')).toEqual({ screen: 'findings', params: { id: 'mover:AbC123' } });
    expect(parseHash('#/findings/mover%3Aabc?from=cabinet')).toEqual({ screen: 'findings', params: { from: 'cabinet', id: 'mover:abc' } });
    expect(parseHash('#/findings?lead=x').params).toEqual({ lead: 'x' });
    const r = { screen: 'findings' as const, params: { id: 'peak:9f' } };
    expect(formatHash(r)).toBe('#/findings/peak%3A9f');
    expect(parseHash(formatHash(r))).toEqual(r);
  });
});
