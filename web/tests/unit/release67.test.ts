import { describe, expect, it } from 'vitest';
import { taskHash, type Task } from '../../src/lib/account';
import { checkHref, type CardFind } from '../../src/screens/findings/cards';
import { taskParams } from '../../src/screens/lab/task';
import { echoOf } from '../../src/screens/planetx/known';
import { searchedPatch, type SearchedField } from '../../src/screens/search/searched';

const task = (t: Partial<Task>): Task => ({ id: 't_1', kind: 'finding', cell_id: null, finding_id: null, field_key: null, ra: 52.0174, dec: 7.8128, state: 'open', result: null, mark_ids: [], created: '', done_at: null, ...t });

describe('a Planet X peak that is a known body seen in one pass', () => {
  // peak:75166a5e0d64 on prod, 19 Nov 2026: listed as a lead "82 au · Eris"; its strongest pass sits on Eris
  const eris = {
    data: {
      peak: {
        by_pass: [
          { sn: 7.7, ra: 27.36803, dec: -0.11144 },
          { sn: 4.1, ra: 26.2408, dec: -0.51489 },
          { sn: 3.4, ra: 27.71206, dec: 0.14647 },
        ],
      },
    },
  };
  it('is an echo of that body', () => {
    expect(echoOf(eris)).toBe('Eris');
  });
  it('is not when the body is only in a weaker pass, or nowhere', () => {
    const weak = { data: { peak: { by_pass: [{ ...eris.data.peak.by_pass[0]!, sn: 3.0 }, eris.data.peak.by_pass[1]!] } } };
    expect(echoOf(weak)).toBeNull();
    expect(echoOf({ data: { peak: { by_pass: [{ sn: 9, ra: 47.14, dec: 8.52 }] } } })).toBeNull();
    expect(echoOf({ data: {} })).toBeNull();
  });
});

describe('where the searched fields gather', () => {
  const f = (ra: number, dec: number): SearchedField => ({ key: `${ra},${dec}`, ra, dec, fov: 20, state: 'done', kinds: ['film'], found: 0, nothing: true, open: { ra, dec, fov: 20 } }) as unknown as SearchedField;
  it('names the patch that holds more than half of them', () => {
    const fields = [...Array.from({ length: 30 }, (_, i) => f(48 + (i % 6), 6 + (i % 5))), ...Array.from({ length: 10 }, (_, i) => f(200 + i * 10, -40))];
    const p = searchedPatch(fields)!;
    expect(p.ra).toBeCloseTo(50.5, 1);
    expect(p.dec).toBeCloseTo(8, 1);
    expect(p.share).toBeCloseTo(0.75);
  });
  it('says nothing when they are spread out, or few', () => {
    expect(searchedPatch(Array.from({ length: 30 }, (_, i) => f(i * 12, 0)))).toBeNull();
    expect(searchedPatch([f(50, 8)])).toBeNull();
  });
});

describe('a track opens straight in Days on the film its search ran on', () => {
  it('the task address carries the service film date, else the card date', () => {
    expect(taskHash(task({ finding_id: 'film:c14d', film: 61075.5 }))).toContain('film=61075.5');
    expect(taskHash(task({ finding_id: 'film:c14d' }), 61075.5)).toContain('film=61075.5');
    expect(taskHash(task({ finding_id: 'film:c14d' }))).not.toContain('film=');
    expect(taskHash(task({ finding_id: 'mover:ab', film: 61075.5 }))).not.toContain('film=');
  });
  it('the lab params of a task carry it too', () => {
    expect(taskParams({ id: 't', kind: 'finding', cell_id: null, finding_id: 'film:c14d', ra: 1, dec: 2, film: 61075.5 })?.['film']).toBe('61075.5');
  });
  it('the card fallback opens the search field on that film', () => {
    const card = { id: 'film:c14d', kind: 'film', ra: 52.0, dec: 7.8, field: { ra: 51.98, dec: 7.83, fov: 20 }, film: 61075.5 } as unknown as CardFind;
    expect(checkHref(card)).toContain('film=61075.5');
    expect(checkHref({ ...card, film: null })).not.toContain('film=');
  });
});
