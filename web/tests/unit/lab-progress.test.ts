import { describe, expect, it } from 'vitest';
import { failWord, retryDelay, searchingLine, type SearchInfo } from '../../src/screens/lab/progress';
import { stageOf, loadStep, STALL_S } from '../../src/screens/lab/loading';

describe('a search that did not answer is asked again by itself', () => {
  it('waits 15 s, 30 s, 60 s, then every 2 minutes, without end', () => {
    expect([0, 1, 2, 3, 4, 10, 100].map(retryDelay)).toEqual([15, 30, 60, 120, 120, 120, 120]);
  });
  it('says who did not answer and when the next try is', () => {
    const outside: SearchInfo = { name: 'known objects', state: 'failed', retry: { nextIn: 25, tryNow: () => {} } };
    expect(failWord(outside)).toBe('slow · asking again in 25 s');
    expect(failWord({ ...outside, ours: true })).toBe('server error · retry 25 s');
    expect(failWord({ ...outside, retry: { nextIn: null, tryNow: () => {} } })).toBe('no answer');
  });
});

describe('the searches on the status line', () => {
  it('names only what still runs, with its share', () => {
    const list: SearchInfo[] = [
      { name: 'movers', state: 'done' },
      { name: 'asteroids', state: 'running', frac: 0.21 },
      { name: 'Planet X', state: 'running', frac: 0.4 },
      { name: 'anomalies', state: 'running' },
    ];
    expect(searchingLine(list)).toBe('searching asteroids 21% · Planet X 40% · anomalies');
    expect(searchingLine([{ name: 'movers', state: 'done' }])).toBe('');
  });
});

describe('the steps of a field being made', () => {
  it('lists where the field is, the reading of the exposures with their count', () => {
    const s = loadStep('reading the exposures: 157 of 581, 404 MB downloaded', 0, 0);
    expect(s.detail).toBe('157 of 581 exposures · 404 MB');
    expect(stageOf(s).at).toBe(1);
    expect(stageOf(loadStep('pass 2 of 3: D4 stacked (9 of 18 detector stacks)', 0, 0)).at).toBe(2);
    expect(stageOf(loadStep('loading the seasons…', 1, 3)).list[0]).toBe('Loading the pictures');
    expect(STALL_S).toBe(20);
  });
});
