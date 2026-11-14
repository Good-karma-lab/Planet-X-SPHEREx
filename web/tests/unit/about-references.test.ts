import { describe, expect, it } from 'vitest';
import { REFERENCES } from '../../src/screens/about/references';

describe('About references', () => {
  const all = REFERENCES.flatMap((g) => g.refs);
  it('lists the sources the site names', () => {
    const text = all.map((r) => r.name).join(' | ');
    for (const need of ['Space Apps', 'SPHEREx', 'Gaia DR3', '2MASS', 'AllWISE', 'CatWISE', 'NEOWISE', 'SIMBAD', 'VizieR', 'NED', 'Small-Body', 'Horizons', 'Minor Planet', 'IRAS', 'AKARI', 'HiPS', 'CADC', 'NEOSSat', 'Batygin & Brown 2016', 'Brown & Batygin 2021', 'Tombaugh', 'Slipher', 'Crill']) {
      expect(text).toContain(need);
    }
  });
  it('gives every entry a note and an https link (the one WiseView page is http only)', () => {
    for (const r of all) {
      expect(r.note.length).toBeGreaterThan(3);
      expect(r.url).toMatch(/^https?:\/\//);
      if (!r.url.includes('byw.tools')) expect(r.url.startsWith('https://')).toBe(true);
    }
  });
  it('does not credit a service no code calls', () => {
    expect(all.map((r) => r.name).join(' ')).not.toMatch(/SkyBoT/);
  });
  it('has no repeated link and name pair', () => {
    expect(new Set(all.map((r) => r.url + r.name)).size).toBe(all.length);
  });
});
