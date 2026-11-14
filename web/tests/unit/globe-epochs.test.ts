import { describe, expect, it } from 'vitest';
import { backdropYear, decimalYear, parseFinderChart, shownPlace } from '../../src/screens/globe/epochs';
import { separation } from '../../src/lib/sky';

// the images IRSA's Finder Chart lists at Barnard's Star (DSS, 2MASS and WISE), trimmed to the fields that are read
const BARNARD = `<?xml version="1.0"?><finderchart status="ok"><result>
<image><surveyname>DSS</surveyname><band>DSS1 Blue</band><obsdate>1950-07-09</obsdate></image>
<image><surveyname>DSS</surveyname><band>DSS1 Red</band><obsdate>1950-07-09</obsdate></image>
<image><surveyname>DSS</surveyname><band>DSS2 Blue</band><obsdate>1988-05-12</obsdate></image>
<image><surveyname>DSS</surveyname><band>DSS2 Red</band><obsdate>1991-06-16</obsdate></image>
<image><surveyname>DSS</surveyname><band>DSS2 IR</band><obsdate>1993-07-26</obsdate></image>
<image><surveyname>2MASS</surveyname><band>J</band><obsdate>2000-05-28</obsdate></image>
<image><surveyname>2MASS</surveyname><band>H</band><obsdate>2000-05-28</obsdate></image>
<image><surveyname>WISE</surveyname><band>w1</band><obsdate>2010-09-14</obsdate></image>
<image><surveyname>WISE</surveyname><band>w3</band><obsdate>2010-03-19</obsdate></image>
</result></finderchart>`;

const barnard = { n: "Barnard's Star", ra: 269.452077, dec: 4.693365, pmra: -801.551, pmdec: 10362.394 };

describe('archive image dates', () => {
  it('reads one date per survey, oldest first', () => {
    const imgs = parseFinderChart(BARNARD);
    expect(imgs.map((x) => `${Math.floor(x.year)} ${x.name}`)).toEqual(['1950 DSS', '1988 DSS blue', '1991 DSS red', '2000 2MASS', '2010 WISE']);
    expect(imgs.find((x) => x.name === 'DSS red')!.backdrop).toBe('dss2');
    expect(imgs.find((x) => x.name === 'WISE')!.year).toBeCloseTo(2010.7, 1);
  });
  it('turns a date into a decimal year', () => {
    expect(decimalYear('2000-01-01')).toBeCloseTo(2000.0014, 3);
    expect(decimalYear('1991-06-16')).toBeCloseTo(1991.455, 2);
    expect(decimalYear('n/a')).toBeNull();
  });
});

describe('the place on the backdrop', () => {
  const imgs = parseFinderChart(BARNARD);
  it('dates each backdrop by its own image, SPHEREx and the dust map by the survey', () => {
    expect(backdropYear('dss2', imgs)).toBeCloseTo(1991.46, 2);
    expect(backdropYear('2mass', imgs)).toBeCloseTo(2000.41, 2);
    expect(backdropYear('dss2')).toBe(1991);
    expect(backdropYear('wise')).toBe(2010.5);
    expect(backdropYear('spherex', imgs)).toBe(2025.9);
    expect(backdropYear('dust', imgs)).toBe(2025.9);
  });
  it("puts Barnard's Star where each picture shows it", () => {
    const dss = shownPlace(barnard, 'dss2', imgs);
    const wise = shownPlace(barnard, 'wise', imgs);
    const spx = shownPlace(barnard, 'spherex', imgs);
    // 10.39″/yr: 3.4′ from the 1991 plate to the 2010 WISE image, 6.0′ from the plate to SPHEREx
    expect(separation(...dss, ...wise) * 60).toBeCloseTo((10.39 * (2010.7 - 1991.46)) / 60, 1);
    expect(separation(...dss, ...spx) * 60).toBeCloseTo((10.39 * (2025.9 - 1991.46)) / 60, 1);
    expect(dss[1]).toBeLessThan(wise[1]);
  });
  it('leaves an object without proper motion at its catalogue place', () => {
    expect(shownPlace({ n: 'x', ra: 10, dec: 20, pmra: null, pmdec: null }, 'dss2')).toEqual([10, 20]);
  });
});
