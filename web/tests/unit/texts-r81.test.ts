import { describe, expect, it } from 'vitest';
import { countWord } from '../../src/screens/lab/progress';
import { listLine, silentWord } from '../../src/screens/findings/finds';

describe('a search count', () => {
  it('is said as not final while what it found is being checked', () => {
    expect(countWord({ name: 'red sources', state: 'done', count: '11', more: true })).toBe('11…');
    expect(countWord({ name: 'red sources', state: 'done', count: '23', more: false })).toBe('23');
    expect(countWord({ name: 'movers', state: 'done' })).toBe('✓');
  });
});

describe('the line over the findings', () => {
  it('says what is shown and what the machine found', () => {
    expect(listLine(48, 104, true, 10408)).toBe('48 of 104 promising finds shown · 10,408 found by the machine in all');
    expect(listLine(null, 1, false, 24684)).toBe('1 find · 24,684 found by the machine in all');
  });
  it('says what SIMBAD is when it is silent', () => {
    expect(silentWord('simbad')).toBe('this star catalogue is not answering; the others are used');
    expect(silentWord('gaia')).toBe('did not answer');
  });
});
