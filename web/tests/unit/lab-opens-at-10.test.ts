import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { firstQuarter } from '../../src/screens/globe/coverage';

// Every way into the lab opens at 10′ (people look a quarter at a time). Kept on purpose: time-lapse tracks at their own size
// ('60'), and the named demo fields of the globe (the Eris field) as they are.
const FILES = [
  'src/lib/aim.ts',
  'src/lib/account.ts',
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
      const bad = lines.filter((l) => /fov:\s*(20|5)\b|fov:\s*'(20|5)'|:\s*'20'[,\s}]/.test(l));
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

describe('a Findings card opens the lab', () => {
  it("at 10' on the object, even when the find carries its search field; a time-lapse keeps its field", async () => {
    const { checkHref } = await import('../../src/screens/findings/cards');
    const field = { ra: 133.76, dec: -7.24219, fov: 60 };
    const bd = { id: 'browndwarf:x', kind: 'browndwarf', ra: 133.75946, dec: -7.24177, field } as never;
    const film = { id: 'film:y', kind: 'film', ra: 60.6, dec: 8.4, field } as never;
    const p = (h: string) => new URLSearchParams(h.split('?')[1]);
    expect(p(checkHref(bd)).get('fov')).toBe('10');
    expect(p(checkHref(bd)).get('ra')).toBe('133.75946');
    expect(p(checkHref(film)).get('fov')).toBe('60');
  });
});

describe('a task from the service opens the lab', () => {
  it("at 10' (a time-lapse at 60')", async () => {
    const { taskHash } = await import('../../src/lib/account');
    const p = (h: string) => new URLSearchParams(h.split('?')[1]);
    expect(p(taskHash({ id: 't1', kind: 'finding', finding_id: 'peak:x', ra: 1, dec: 2 } as never)).get('fov')).toBe('10');
    expect(p(taskHash({ id: 't2', kind: 'finding', finding_id: 'film:y', ra: 1, dec: 2 } as never)).get('fov')).toBe('60');
    expect(p(taskHash({ id: 't3', kind: 'red', ra: 1, dec: 2 } as never)).get('fov')).toBe('10');
    expect(p(taskHash({ id: 't4', kind: 'cell', cell_id: '284-0802', quarter: 'NE', ra: 1, dec: 2 } as never)).get('fov')).toBe('10');
  });
});
