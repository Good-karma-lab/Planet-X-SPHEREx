import { describe, expect, it } from 'vitest';
import { isSquareTask, selectsTargetOnOpen } from '../../src/screens/lab/rules';

describe('a square task has no object of its own', () => {
  // the three links a square task arrives by: the globe's, the lab's own "next", the cabinet's
  const links: Record<string, string>[] = [
    { ra: '77.14286', dec: '30.5', fov: '20', task: 'cell', cell: '361-0199' },
    { ra: '77.14286', dec: '30.5', fov: '20', task: 't_adeea9f6d109dbcd', kind: 'cell', cell: '361-0199' },
    { ra: '77.14286', dec: '30.5', fov: '20', task: 't_adeea9f6d109dbcd' },
  ];
  it('selects nothing when the square opens, so the 2′ inset waits for a clicked source (RW Aurigae lies in square 361-0199)', () => {
    for (const p of links) {
      expect(isSquareTask(p)).toBe(true);
      expect(selectsTargetOnOpen(p)).toBe(false);
    }
  });
  it('keeps the test target selected in a plain lab and in a task about one object', () => {
    expect(selectsTargetOnOpen({ ra: '77.14286', dec: '30.5', fov: '20' })).toBe(true);
    expect(selectsTargetOnOpen({ ra: '269.4', dec: '4.7', fov: '5', task: 'moves', finding: 'mover:d7d04b66326e' })).toBe(true);
    expect(selectsTargetOnOpen({ ra: '269.4', dec: '4.7', fov: '5', task: 't_1', kind: 'finding', finding: 'film:1' })).toBe(true);
  });
});
