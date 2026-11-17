import { describe, expect, it } from 'vitest';
import { matchSquare, quarterCentre, type GridCell } from '../../src/screens/lab/square';
import { loadStep } from '../../src/screens/lab/loading';

// square 099-0543 as the grid API gives it
const CELL: GridCell = {
  id: '099-0543',
  ra: 331.06599,
  dec: -56.833333,
  corners: [
    [330.761421, -57.0],
    [331.370558, -57.0],
    [331.370558, -56.666667],
    [330.761421, -56.666667],
  ],
};

describe('a grid square and its quarters', () => {
  it('puts a quarter midway between the centre and its corner (north +Dec, east +RA)', () => {
    const [r, d] = quarterCentre(CELL, 'NE')!;
    expect(r).toBeCloseTo((331.06599 + 331.370558) / 2, 5);
    expect(d).toBeCloseTo((-56.833333 - 56.666667) / 2, 5);
    const sw = quarterCentre(CELL, 'SW')!;
    expect(sw[0]).toBeLessThan(CELL.ra);
    expect(sw[1]).toBeLessThan(CELL.dec);
  });
  it('knows a 20′ field at the centre as the square and a 10′ field at a quarter centre as that quarter', () => {
    expect(matchSquare([CELL], CELL.ra, CELL.dec, 20)).toEqual({ cell: CELL, quarter: null });
    const [r, d] = quarterCentre(CELL, 'SE')!;
    expect(matchSquare([CELL], r, d, 10)?.quarter).toBe('SE');
    // any other field is no square: nothing is counted there
    expect(matchSquare([CELL], CELL.ra + 0.01, CELL.dec, 20)).toBeNull();
    expect(matchSquare([CELL], CELL.ra, CELL.dec, 10)).toBeNull();
    expect(matchSquare([CELL], CELL.ra, CELL.dec, 5)).toBeNull();
  });
});

describe('the progress of a field being made', () => {
  it('says what the service does, with numbers and a bar where it gives them', () => {
    expect(loadStep('reading the exposures: 157 of 581, 404 MB downloaded', 0, 0)).toEqual({ title: 'Loading the exposures', detail: '157 of 581 · 404 MB', frac: 157 / 581 });
    expect(loadStep('pass 1 of 3: D4 stacked (5 of 18 detector stacks)', 0, 0)).toEqual({ title: 'Stacking pass 1 of 3', detail: '5 of 18 detector stacks', frac: 5 / 18 });
    expect(loadStep('Request queued · 2 ahead', 0, 0)).toMatchObject({ title: 'Request queued', detail: '2 ahead', frac: null });
    expect(loadStep('loading the seasons…', 1, 3)).toMatchObject({ title: 'Loading the pictures', detail: '1 of 3 passes' });
    expect(loadStep('failed: none of the 12 exposures could be read', 0, 0)).toMatchObject({ failed: true });
  });
});
