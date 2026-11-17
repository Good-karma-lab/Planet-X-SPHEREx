import { describe, expect, it } from 'vitest';
import { alsoLine, orderRows, unexplainedCount, type FindRow } from '../../src/screens/lab/candidates';

const row = (key: string, status: FindRow['status']): FindRow => ({ key, name: key, line: '', status, uv: null, pick: () => {} });

describe('the Candidates list of a field', () => {
  // Barnard 5′ as the user saw it: the star (known) and an asteroid track of one pass
  it('counts only the unexplained finds: a known star never counts, nor a track JPL did not answer for', () => {
    expect(unexplainedCount([row('Barnard', 'known'), row('track', 'jpl')])).toBe(0);
    expect(unexplainedCount([row('Barnard', 'known'), row('track', 'unexplained'), row('red', 'checking')])).toBe(1);
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
