import { describe, expect, it } from 'vitest';
import { plainPhase } from '../../src/lib/plain';

describe('plainPhase', () => {
  it('says the queue in plain words', () => {
    expect(plainPhase('waiting for a free worker')).toBe('Request queued');
    expect(plainPhase('waiting for a free worker (2 ahead)')).toBe('Request queued · 2 ahead');
  });
  it('never shows a bare "starting"', () => {
    expect(plainPhase('starting')).toBe('Finding the exposures…');
  });
  it('says the scan without blocks or jobs', () => {
    expect(plainPhase('scanning distance and own motion: block 3 of 96 (12 s, about 358 s left)')).toBe('Searching every distance and motion: 3 of 96 (about 358 s left)');
    expect(plainPhase('scanning distance and own motion: 96 blocks, about 313 s')).toBe('Searching every distance and motion: about 313 s');
    expect(plainPhase('summing along the orbits: job 24 of 36 (62 s)')).toBe('Adding the passes along the orbits: 24 of 36');
  });
  it('keeps other words as they are', () => {
    expect(plainPhase('pass 1 of 3: D1 stacked')).toBe('pass 1 of 3: D1 stacked');
    expect(plainPhase(undefined)).toBe('');
  });
});
