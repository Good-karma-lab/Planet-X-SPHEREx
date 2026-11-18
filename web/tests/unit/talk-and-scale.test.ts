import { describe, expect, it } from 'vitest';
import { pieces } from '../../src/screens/findings/talk';
import { scaleBar } from '../../src/screens/lab';

describe('Talk: links in a comment', () => {
  it('turns links into links, keeping the sentence around them', () => {
    expect(pieces('see https://irsa.ipac.caltech.edu/x. ok')).toEqual([
      { t: 'see ' },
      { t: 'irsa.ipac.caltech.edu/x', href: 'https://irsa.ipac.caltech.edu/x' },
      { t: '. ok' },
    ]);
    expect(pieces('no links here')).toEqual([{ t: 'no links here' }]);
  });
});

describe('the scale bar', () => {
  it('is a round length near a fifth of the field', () => {
    expect(scaleBar(10, 500)).toEqual({ arcmin: 2, px: 100, word: '2′' });
    expect(scaleBar(60, 600).word).toBe('10′');
    expect(scaleBar(2, 400).word).toBe('30″');
  });
});

import { quickPlace } from '../../src/screens/lab';
describe("the first look's place on the picture", () => {
  it('lies on the box when both grids agree, and is shifted by the snap when they do not', () => {
    const g = { ra0: 100, dec0: 0, step_arcsec: 6, n: 100 }; // a 10′ field
    expect(quickPlace(g, g)).toBeUndefined();
    // the first look's centre 30″ north of the field's: its box lies 5 % higher
    const p = quickPlace({ ...g, dec0: 30 / 3600 }, g)!;
    expect(p.top).toBe('-5%');
    expect(p.left).toBe('0%');
  });
});
