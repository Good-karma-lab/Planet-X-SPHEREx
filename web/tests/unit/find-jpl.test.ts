import { describe, expect, it } from 'vitest';
import { withFindJpl } from '../../src/screens/lab/known';

describe("a find's catalogue line takes JPL's answer from the find's own check", () => {
  it('drops a JPL that is still being asked when the find was checked', () => {
    expect(withFindJpl('no match so far · JPL did not answer', 'none')).toBe('in no catalogue · JPL: no known body there');
    expect(withFindJpl('no match so far · CatWISE, JPL being asked', 'none')).toBe('no match so far · CatWISE being asked · JPL: no known body there');
    expect(withFindJpl('no match so far · JPL, 2MASS did not answer', 'none')).toBe('no match so far · 2MASS did not answer · JPL: no known body there');
  });
  it('leaves the line as it is when the find has no JPL answer, or JPL is not in it', () => {
    expect(withFindJpl('no match so far · JPL did not answer', 'unavailable')).toBe('no match so far · JPL did not answer');
    expect(withFindJpl('known · SIMBAD Barnard’s star', 'none')).toBe('known · SIMBAD Barnard’s star');
  });
});

import { withFindStatus } from '../../src/screens/lab/known';
describe("an unexplained find's catalogue line never says known for a catalogue that names nothing", () => {
  it('says what the catalogue did', () => {
    expect(withFindStatus('known · in CatWISE2020', 'unexplained')).toBe('in CatWISE2020, which does not say what it is');
    expect(withFindStatus('known · SIMBAD Barnard’s star', 'unexplained')).toBe('known · SIMBAD Barnard’s star');
    expect(withFindStatus('known · in CatWISE2020', 'known')).toBe('known · in CatWISE2020');
  });
});
