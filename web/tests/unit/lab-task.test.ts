import { describe, expect, it } from 'vitest';
import { stepAt, taskOfParams, taskParams, TREES } from '../../src/screens/lab/task';

describe('the lab task of an address', () => {
  it('is absent without a task parameter', () => {
    expect(taskOfParams({ ra: '1', dec: '2', fov: '5' })).toBeNull();
  });
  it('reads a task id with its kind and square', () => {
    expect(taskOfParams({ task: 't_1', kind: 'cell', cell: 'c9' })).toEqual({ id: 't_1', kind: 'cell', cell_id: 'c9', finding_id: null });
  });
  it('reads the kind given as the task (the Findings link): the lab takes the task itself', () => {
    expect(taskOfParams({ task: 'finding', finding: 'anomaly:ab' })).toMatchObject({ id: null, kind: 'finding', finding_id: 'anomaly:ab' });
    // there is no submit task in the lab: the submit is in the Investigation page of the Cabinet
    expect(taskOfParams({ task: 'submit', fov: '20' })?.kind).toBe('cell');
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

describe('a tree of plain questions', () => {
  it('walks a red source from the first question to the spectrum', () => {
    const tree = TREES.red!;
    expect(stepAt(tree, [])?.id).toBe('shape');
    expect(stepAt(tree, ['sharp'])?.id).toBe('every_pass');
    expect(stepAt(tree, ['sharp', 'yes', 'no'])?.id).toBe('spectrum');
    // an answer that ends the path leaves no further step; nor does an answer the step does not have
    expect(stepAt(tree, ['fuzzy'])).toBeNull();
    expect(stepAt(tree, ['sharp', 'maybe'])).toBeNull();
  });
  it('ends every step with "Not sure", which is "Can’t tell"', () => {
    for (const tree of [TREES.red!, TREES.anomaly!])
      for (const s of tree) expect(s.answers[s.answers.length - 1]).toEqual({ id: 'not_sure', label: 'Not sure', result: 'cant_tell' });
  });
});
