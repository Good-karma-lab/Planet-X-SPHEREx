import { describe, expect, it } from 'vitest';
import { SERIES, SERIES_OF_KIND, stepAt, taskOfParams, taskParams } from '../../src/screens/lab/task';

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

describe('a question series (spec §5e)', () => {
  it('walks a track: some pictures missing, then dot or streak', () => {
    const track = SERIES.track!;
    expect(stepAt(track, [])).toMatchObject({ id: 'sum', show: 'sums' });
    expect(stepAt(track, ['yes'])).toMatchObject({ id: 'steps', show: 'blink' });
    expect(stepAt(track, ['yes', 'some'])).toMatchObject({ id: 'missing', tap: true });
    expect(stepAt(track, ['yes', 'some', 'done'])?.id).toBe('shape');
    expect(stepAt(track, ['yes', 'every'])?.id).toBe('shape');
    expect(stepAt(track, ['yes', 'no'])).toBeNull();
    expect(stepAt(track, ['no'])).toBeNull();
  });
  it('asks only what an eye judges: no spectrum classes, detector places or exposure counts', () => {
    const words = Object.values(SERIES)
      .flat()
      .map((s) => s.question)
      .join(' ');
    expect(words).not.toMatch(/detector|exposures|spectrum|brown dwarf|dusty/i);
  });
  it('ends every step that is not a tap with "Not sure"', () => {
    for (const steps of Object.values(SERIES))
      for (const s of steps) if (!s.tap) expect(s.answers[s.answers.length - 1]).toEqual({ id: 'not_sure', label: 'Not sure', result: 'cant_tell' });
  });
  it('knows the series of a find by its kind', () => {
    expect(SERIES_OF_KIND['film']).toBe('track');
    expect(SERIES_OF_KIND['browndwarf']).toBe('slow');
    expect(SERIES_OF_KIND['peak']).toBe('peak');
  });
});
