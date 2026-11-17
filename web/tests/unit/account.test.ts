import { describe, expect, it } from 'vitest';
import { describeHash, taskHash, type Task } from '../../src/lib/account';
import { markHash } from '../../src/screens/cabinet/Cabinet';

const task = (t: Partial<Task>): Task => ({ id: 't_1', kind: 'cell', cell_id: null, finding_id: null, field_key: null, ra: null, dec: null, state: 'open', result: null, mark_ids: [], created: '', done_at: null, ...t });

describe('the view history knows a place from its hash', () => {
  it('a lab field is one place whatever its display state', () => {
    const a = describeHash('#/lab?ra=269.4486&dec=4.7379&fov=20&band=2');
    const b = describeHash('#/lab?ra=269.4486&dec=4.7379&fov=20&band=5&pass=3');
    expect(a?.kind).toBe('lab');
    expect(a?.key).toBe(b?.key);
    expect(a?.title).toBe('Lab · RA 269.449° Dec +4.738° · 20′');
    expect(describeHash('#/lab?ra=269.4486&dec=4.7379&fov=5')?.key).not.toBe(a?.key);
  });
  it('a finding and a square are places; a bare screen is not', () => {
    expect(describeHash('#/findings?lead=mover%3A6909a2ee0124')).toEqual({ kind: 'finding', title: 'Moving object · 6909a2', key: 'finding:mover:6909a2ee0124' });
    expect(describeHash('#/findings?lead=film%3A8dcb8e38bc95')?.title).toBe('Time-lapse track · 8dcb8e');
    expect(describeHash('#/explore?cell=b10c20')?.kind).toBe('cell');
    for (const h of ['#/lab', '#/findings', '#/explore', '#/me', '', '#/lab?ra=x&dec=1']) expect(describeHash(h)).toBeNull();
  });
});

describe('where a task and a mark open', () => {
  it('a square opens at 10′ at its centre with the task', () => {
    expect(taskHash(task({ cell_id: 'b10c20', ra: 10.5, dec: -3.25 }))).toBe('#/lab?ra=10.50000&dec=-3.25000&fov=10&task=t_1&kind=cell&cell=b10c20');
  });
  it('a finding without a place opens its page', () => {
    expect(taskHash(task({ kind: 'finding', finding_id: 'mover:1' }))).toBe('#/findings?lead=mover%3A1');
    expect(taskHash(task({ kind: 'finding', finding_id: 'mover:1', ra: 1, dec: 2 }))).toBe('#/lab?ra=1.00000&dec=2.00000&fov=10&task=t_1&kind=finding&finding=mover%3A1');
  });
  it('a mark opens its field when the key carries the place, else its own position', () => {
    expect(markHash({ field_key: '269.4486,+4.7379,20', ra: 269.45, dec: 4.74 })).toBe('#/lab?ra=269.4486&dec=4.7379&fov=20');
    expect(markHash({ field_key: 'ffffffffffffffffffffffff', ra: 10.5, dec: -3.25 })).toBe('#/lab?ra=10.50000&dec=-3.25000&fov=5');
  });
});
