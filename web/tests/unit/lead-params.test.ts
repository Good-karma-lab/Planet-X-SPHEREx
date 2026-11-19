import { describe, expect, it } from 'vitest';
import { leadParams, taskHash } from '../../src/lib/account';

describe('A square given for a lead (task.why)', () => {
  const why = { finding_id: 'peak:3cad09c030ce', kind: 'peak', promise: 86, ra: 12.3456789, dec: -4.5, text: 'a Planet X peak · promise 86', ask: { kind: 'moves', finding_id: 'peak:3cad09c030ce', series: 'peak' } };
  it('carries the lead, the kind of its series and its place', () => {
    expect(leadParams(why)).toEqual({ lead: 'peak:3cad09c030ce', lead_kind: 'moves', lead_text: 'a Planet X peak · promise 86', lead_ra: '12.34568', lead_dec: '-4.50000' });
  });
  it('a square task without why has no lead in its address', () => {
    const t = { id: 't1', kind: 'cell', cell_id: '099-0543', quarter: 'NE', ra: 1, dec: 2, fov: 10, finding_id: null, state: 'open', result: null, mark_ids: [] } as never;
    expect(taskHash(t)).not.toContain('lead');
    expect(taskHash({ ...(t as object), why } as never)).toContain('lead=peak%3A3cad09c030ce&lead_kind=moves');
  });
});
