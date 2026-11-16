import { describe, expect, it } from 'vitest';
import { CHECK_IDS, checksStatus, parseChecks, taskOfParams, taskParams } from '../../src/screens/lab/task';

describe('the lab task of an address', () => {
  it('is absent without a task parameter', () => {
    expect(taskOfParams({ ra: '1', dec: '2', fov: '5' })).toBeNull();
  });
  it('reads a task id with its kind and square', () => {
    expect(taskOfParams({ task: 't_1', kind: 'cell', cell: 'c9' })).toEqual({ id: 't_1', kind: 'cell', cell_id: 'c9', finding_id: null, investigation_id: null });
  });
  it('reads the kind given as the task (the Findings link): the lab takes the task itself', () => {
    expect(taskOfParams({ task: 'finding', finding: 'anomaly:ab' })).toMatchObject({ id: null, kind: 'finding', finding_id: 'anomaly:ab' });
    expect(taskOfParams({ task: 'submit', inv: 'i1' })).toMatchObject({ id: null, kind: 'submit', investigation_id: 'i1' });
  });
  it('guesses the kind of a bare task id from the field size (the cabinet link: a square 20′, a finding 5′)', () => {
    expect(taskOfParams({ task: 't_2', fov: '20' })?.kind).toBe('cell');
    expect(taskOfParams({ task: 't_3', fov: '5' })?.kind).toBe('finding');
  });
  it('makes the lab address of the next task', () => {
    expect(taskParams({ id: 't_4', kind: 'cell', cell_id: 'c1', finding_id: null, ra: 10, dec: -5 })).toEqual({
      ra: '10.00000', dec: '-5.00000', fov: '20', task: 't_4', kind: 'cell', cell: 'c1',
    });
    expect(taskParams({ id: 't_5', kind: 'finding', cell_id: null, finding_id: 'f', ra: null, dec: null })).toBeNull();
  });
});

// A fake of `GET /api/checks/{job}` (product spec §8, §6).
const FAKE = {
  state: 'running',
  checks: [
    { id: 'two_passes', state: 'pass', detail: 'seen in 3 passes' },
    { id: 'gaia', state: 'warn', detail: 'a Gaia star 5″ away' },
    { id: 'neowise', state: 'no_answer', detail: '' },
    { id: 'made_up', state: 'pass', detail: '' },
  ],
  can_submit: false,
};

describe('the checks before submit', () => {
  it('always lists the eight checks in order; one with no word yet is waiting', () => {
    const v = parseChecks(FAKE, 'j1');
    expect(v.checks.map((c) => c.id)).toEqual([...CHECK_IDS]);
    expect(v.checks.find((c) => c.id === 'gaia')).toEqual({ id: 'gaia', state: 'warn', detail: 'a Gaia star 5″ away' });
    expect(v.checks.find((c) => c.id === 'jpl')?.state).toBe('waiting');
    expect(v.state).toBe('running');
    expect(checksStatus(v)).toBe('2 of 8 answered');
  });
  it('says what blocks the submit', () => {
    const all = (state: string) => ({ state: 'done', can_submit: state === 'pass', checks: CHECK_IDS.map((id) => ({ id, state, detail: '' })) });
    expect(checksStatus(parseChecks(all('pass'), 'j'))).toBe('All answered');
    expect(parseChecks(all('pass'), 'j').can_submit).toBe(true);
    expect(checksStatus(parseChecks(all('warn'), 'j'))).toBe('All answered · a warning needs a comment');
    expect(checksStatus(parseChecks(all('fail'), 'j'))).toBe('8 of 8 answered · a failed check blocks the submit');
  });
});
