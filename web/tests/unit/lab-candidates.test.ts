import { describe, expect, it } from 'vitest';
import { alsoLine, findOfRow, orderRows, unexplainedCount, type FindRow } from '../../src/screens/lab/candidates';

const row = (key: string, status: FindRow['status']): FindRow => ({ key, name: key, line: '', status, uv: null, pick: () => {} });

describe('the Candidates list of a field', () => {
  // Barnard 5′ as the user saw it: the star (known) and an asteroid track of one pass
  it('counts only the unexplained finds: a known star never counts, nor a track JPL did not answer for', () => {
    expect(unexplainedCount([row('Barnard', 'known'), row('track', 'jpl')])).toBe(0);
    expect(unexplainedCount([row('Barnard', 'known'), row('track', 'unexplained'), row('red', 'checking')])).toBe(1);
  });
  it('never counts a red source or a spectral anomaly: they are the folded group, as in Findings', () => {
    const red: FindRow = { ...row('red 1', 'unexplained'), group: 'red', ra: 27.38, dec: -0.12 };
    const an: FindRow = { ...row('anomaly 1', 'unexplained'), group: 'anomaly', ra: 27.4, dec: -0.1 };
    expect(unexplainedCount([red, an, row('track', 'unexplained')])).toBe(1);
    // a row can start its question task when the service has a find of its kind at its place (within 6″)
    const finds = [
      { id: 'red:abc', kind: 'red', ra: 27.3801, dec: -0.1201 },
      { id: 'anomaly:far', kind: 'anomaly', ra: 27.5, dec: -0.1 },
    ];
    expect(findOfRow(red, finds)).toBe('red:abc');
    expect(findOfRow(an, finds)).toBeNull();
    expect(findOfRow(row('track', 'unexplained'), finds)).toBeNull();
  });
  it('lists the unexplained first, the known last, the order of the searches kept inside each', () => {
    const rows = [row('a', 'known'), row('b', 'unexplained'), row('c', 'jpl'), row('d', 'unexplained'), row('e', 'checking'), row('f', 'known')];
    expect(orderRows(rows).map((r) => r.key)).toEqual(['b', 'd', 'c', 'e', 'a', 'f']);
  });
  it('says the searches with nothing found in one quiet line', () => {
    expect(
      alsoLine([
        { name: 'movers', state: 'found' },
        { name: 'brown dwarfs', state: 'nothing' },
        { name: 'pairs', state: 'nothing' },
        { name: 'Planet X', state: 'running' },
      ]),
    ).toBe('Also searched: brown dwarfs, pairs: nothing · Still searching: Planet X');
    expect(alsoLine([{ name: 'movers', state: 'found' }])).toBe('');
  });
});
