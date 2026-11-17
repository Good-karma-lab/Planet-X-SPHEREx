import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { firstQuarter } from '../../src/screens/globe/coverage';

// Every way into the lab opens at 10′ (people look a quarter at a time). Kept on purpose: time-lapse tracks at their own size
// ('60'), and the named demo fields of the globe (the Eris field) as they are.
const FILES = [
  'src/screens/globe.tsx',
  'src/screens/globe/coverage.ts',
  'src/screens/index/IndexV2.tsx',
  'src/screens/findings/cards.tsx',
  'src/screens/findings/finds.tsx',
];

describe('the lab opens at 10′', () => {
  it('no 20′ default is left where the lab is opened', () => {
    for (const f of FILES) {
      const lines = readFileSync(f, 'utf8')
        .split('\n')
        .filter((l) => !/Eris|demo/i.test(l));
      const bad = lines.filter((l) => /fov:\s*20\b|fov:\s*'20'|:\s*'20'[,\s}]/.test(l));
      expect(bad, f).toEqual([]);
    }
  });

  it("a square's first quarter is its north-east one, at 10′, inside the square", () => {
    const q = firstQuarter('294-0148', { ra: 50, dec: 8 });
    expect(q.fov).toBe(10);
    expect(q.quarter).toBe('NE');
    expect(q.dec).toBeGreaterThan(8.0);
    expect(q.dec).toBeLessThan(8.3334);
  });
});
