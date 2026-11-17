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
