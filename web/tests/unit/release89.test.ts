import { describe, expect, it } from 'vitest';
import { taskHash, type Task } from '../../src/lib/account';
import { ErrorBoundary } from '../../src/ui/ErrorBoundary';

const T = { id: 't_1', kind: 'moves', finding_id: 'peak:1', cell_id: null, ra: 1, dec: 2, fov: 10, state: 'open', mark_ids: [] } as unknown as Task;

describe('83.9', () => {
  it('an open question task opens on its own find; a square does not carry one', () => {
    expect(taskHash(T)).toContain('finding=peak');
    expect(taskHash({ ...T, kind: 'cell', cell_id: 'x' } as Task)).not.toContain('finding=');
  });
  it('a continued square carries its lead', () => {
    const t = { ...T, kind: 'cell', finding_id: null, cell_id: 'x', why: { finding_id: 'peak:1', ask: { kind: 'moves', finding_id: 'peak:1' } } } as unknown as Task;
    expect(taskHash(t)).toContain('lead=peak');
  });
  it('a failed screen is another try at another place, and stays failed at the same one', () => {
    const b = new ErrorBoundary({ name: 'lab', at: 'b', children: null } as never);
    const said: unknown[] = [];
    b.setState = ((s: unknown) => said.push(s)) as never;
    b.state = { failed: true };
    b.props = { name: 'lab', at: 'b', children: null } as never;
    b.componentDidUpdate({ at: 'b' });
    expect(said).toEqual([]);
    b.componentDidUpdate({ at: 'a' });
    expect(said).toEqual([{ failed: false }]);
  });
});
