import tnoJson from './tno.json';

export interface Elements {
  a: number;
  e: number;
  i: number;
  om: number;
  w: number;
}

export interface Planet extends Elements {
  name: string;
  /** mean longitude at J2000 and its rate (degrees per Julian century), longitude of perihelion */
  L: number;
  dL: number;
  vp: number;
  colour: string;
  /** drawn radius, px at the default zoom (bodies are enlarged) */
  size: number;
}

/** The planets: JPL's mean elements (J2000, a in au). */
export const PLANETS: Planet[] = [
  { name: 'Mercury', a: 0.3871, e: 0.20563, i: 7.005, om: 48.331, w: 77.456 - 48.331, L: 252.25, dL: 149472.67, vp: 77.456, colour: '#b9b2a8', size: 2.2 },
  { name: 'Venus', a: 0.72333, e: 0.00677, i: 3.395, om: 76.68, w: 131.53 - 76.68, L: 181.98, dL: 58517.82, vp: 131.53, colour: '#e8cf9a', size: 2.8 },
  { name: 'Earth', a: 1.0, e: 0.01671, i: 0, om: -11.26, w: 102.94 + 11.26, L: 100.46, dL: 35999.37, vp: 102.94, colour: '#6fb3ff', size: 3 },
  { name: 'Mars', a: 1.52368, e: 0.0934, i: 1.85, om: 49.558, w: 336.06 - 49.558, L: 355.45, dL: 19140.3, vp: 336.06, colour: '#d9805a', size: 2.6 },
  { name: 'Jupiter', a: 5.2026, e: 0.04849, i: 1.303, om: 100.464, w: 14.331 - 100.464, L: 34.4, dL: 3034.75, vp: 14.331, colour: '#d8b48a', size: 5 },
  { name: 'Saturn', a: 9.55491, e: 0.05551, i: 2.489, om: 113.666, w: 93.057 - 113.666, L: 49.94, dL: 1222.49, vp: 93.057, colour: '#e3cf9d', size: 4.6 },
  { name: 'Uranus', a: 19.2184, e: 0.0463, i: 0.773, om: 74.006, w: 173.005 - 74.006, L: 313.23, dL: 428.48, vp: 173.005, colour: '#8fd8e0', size: 4 },
  { name: 'Neptune', a: 30.1104, e: 0.00899, i: 1.77, om: 131.784, w: 48.124 - 131.784, L: 304.88, dL: 218.46, vp: 48.124, colour: '#5f86ff', size: 4 },
];

export interface Tno extends Elements {
  n: string;
  ma: number;
  epoch: number;
}

/** Dwarf planets and the distant bodies whose orbits cluster: JPL Small-Body Database, osculating elements fetched 2026-11-14. */
export const TNOS = tnoJson as Tno[];

/** Planet Nine as predicted by Brown & Batygin (2021): not discovered. */
export const P9: Elements & { mass: string; peri: number; aRange: [number, number] } = {
  a: 380,
  e: 1 - 300 / 380,
  i: 16,
  om: 97,
  w: 254 - 97,
  mass: '6.2 Earth masses',
  peri: 300,
  aRange: [300, 520],
};

export interface Field {
  id: string;
  name: string;
  why: string;
  ra: number;
  dec: number;
  /** field size, arcminutes */
  size: number;
  /** distance range of the search, au */
  dmin: number;
  dmax: number;
  colour: string;
}

/** The directions this site searched for Planet Nine and the body that proves the search works. */
export const FIELDS: Field[] = [
  { id: 'ecl', name: 'Ecliptic D4 field', why: 'inside the band of Planet Nine’s predicted orbit', ra: 60.7116, dec: 8.617, size: 60, dmin: 250, dmax: 2000, colour: '#5fe3d6' },
  { id: 'iras', name: 'IRAS/AKARI field', why: 'a second direction searched for distant bodies', ra: 34.72, dec: -49.79, size: 60, dmin: 250, dmax: 2000, colour: '#7dd3fc' },
  { id: 'sedna', name: 'Sedna (known body)', why: 'V 20.8: the proof that the search finds a body this faint', ra: 61.7725, dec: 8.8408, size: 18, dmin: 75, dmax: 92, colour: '#6fdc8c' },
];

/** What was recorded for the three directions (the validation of the search; dated 2026-11-14). */
export const RECORDED: Record<string, { verdict: 'ruled out' | 'found' | 'not a body'; title: string; text: string; rows?: [string, string][] }> = {
  ecl: {
    verdict: 'ruled out',
    title: '394 au · D4',
    text: 'A peak that passed the bar of the Planet Nine search: 0.034 ± 0.006 mJy in D4 (RA 60.94, Dec +8.39, Aug 2025). The NEOWISE archive (2014–2024, about 900 exposures) added along its orbit gives −0.0005 ± 0.0031 mJy, where a body of that flux would stand at 13.6σ.',
    rows: [
      ['SPHEREx D4', '0.034 ± 0.006 mJy'],
      ['NEOWISE along the orbit', '−0.0005 ± 0.0031 mJy'],
      ['Expected if real', '13.6σ'],
    ],
  },
  sedna: {
    verdict: 'found',
    title: 'Sedna · 82.9 au',
    text: 'Found blind: 4.4 million orbits per sky point (75–92 au); 6.6σ in the sum of three passes under a ceiling of 8.1σ, 9.1σ in photometry (0.031 ± 0.003 mJy); JPL gives 83.0 au. Control tracks beside it: 0 of 8 reach the bar.',
  },
};

/** Known distant bodies found blind: the proof of the method. */
export const PROOF: { who: string; what: string; got: string }[] = [
  { who: 'Eris', what: 'V 18.7 · 3 of 3 passes, then the deep sum', got: '95.89 ± 0.54 au (JPL 95.5) · deep sum 20.3σ' },
  { who: 'Makemake', what: 'V 17.1 · linked passes', got: '52.38 ± 0.23 au · JPL 52.3' },
  { who: 'Sedna', what: 'V 20.8 · sum of 3 passes', got: '9.1σ · 82.9 au · JPL 83.0' },
];
