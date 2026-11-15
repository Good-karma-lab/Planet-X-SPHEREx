// Dates of the archive images of a place, and where a moving star stands on the backdrop shown.
//
// A backdrop of the globe is a picture taken on some date: a pin (and the click that finds it) must stand where the object was
// on that date, or it sits beside the star (Barnard's Star moves 10.4″/yr: 6′ between the DSS2 red plate and SPHEREx). The
// dates are those of the images at the object's place, from IRSA's Finder Chart service (the plate of DSS1, DSS2 blue and red,
// 2MASS, WISE), kept in the browser once known; until they are in, the middle year of each survey.

import { SURVEY_EPOCH } from '../../lib/sky';
import { BACKDROP_YEAR, placeAt } from '../search/path';

export interface ArchiveImage {
  /** decimal year */
  year: number;
  /** survey as people say it: DSS, DSS blue, DSS red, 2MASS, WISE */
  name: string;
  /** the globe backdrop this image is the date of, if any */
  backdrop: string | null;
}

const FINDER = 'https://irsa.ipac.caltech.edu/applications/finderchart/servlet/api';
const STORE = 'globe.imagedates.v1.';

/** "1991-06-16" → 1991.455 */
export function decimalYear(date: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date.trim());
  if (!m) return null;
  const y = Number(m[1]);
  const t0 = Date.UTC(y, 0, 1);
  const t1 = Date.UTC(y + 1, 0, 1);
  return y + (Date.UTC(y, Number(m[2]) - 1, Number(m[3])) + 43200000 - t0) / (t1 - t0);
}

// one date per survey: the band whose plate or scan the globe's colour backdrop is dated by
const BANDS: Record<string, { name: string; backdrop: string | null }> = {
  'dss1 red': { name: 'DSS', backdrop: null },
  'dss1 blue': { name: 'DSS', backdrop: null },
  'dss2 blue': { name: 'DSS blue', backdrop: null },
  'dss2 red': { name: 'DSS red', backdrop: 'dss2' },
  j: { name: '2MASS', backdrop: '2mass' },
  w1: { name: 'WISE', backdrop: 'wise' },
};

/** The images of a Finder Chart answer (XML), one per survey, oldest first. */
export function parseFinderChart(xml: string): ArchiveImage[] {
  const out: ArchiveImage[] = [];
  const seen = new Set<string>();
  for (const m of xml.matchAll(/<image>([\s\S]*?)<\/image>/g)) {
    const band = /<band>\s*([^<]*?)\s*<\/band>/.exec(m[1]!)?.[1]?.toLowerCase() ?? '';
    const date = /<obsdate>\s*([^<]*?)\s*<\/obsdate>/.exec(m[1]!)?.[1] ?? '';
    const b = BANDS[band];
    const year = decimalYear(date);
    if (!b || year === null || seen.has(b.name)) continue;
    seen.add(b.name);
    out.push({ year, name: b.name, backdrop: b.backdrop });
  }
  return out.sort((p, q) => p.year - q.year);
}

const known = new Map<string, ArchiveImage[] | null>();
const asking = new Set<string>();
const waiting: (() => void)[] = [];
let running = 0;
const listeners = new Set<() => void>();

const read = (key: string): ArchiveImage[] | undefined => {
  try {
    const s = localStorage.getItem(STORE + key);
    return s ? (JSON.parse(s) as ArchiveImage[]) : undefined;
  } catch {
    return undefined;
  }
};

async function ask(key: string, ra: number, dec: number): Promise<void> {
  // two at a time: a place IRSA has not seen takes up to a minute, a known one about a second
  if (running >= 2) await new Promise<void>((r) => waiting.push(r));
  running++;
  try {
    const q = new URLSearchParams({ locstr: `${ra.toFixed(5)} ${dec.toFixed(5)}`, survey: 'DSS,2MASS,WISE', subsetsize: '1', mode: 'prog' });
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 120000);
    const r = await fetch(`${FINDER}?${q}`, { signal: ctl.signal, credentials: 'omit' });
    clearTimeout(t);
    const imgs = r.ok ? parseFinderChart(await r.text()) : [];
    known.set(key, imgs.length ? imgs : null);
    if (imgs.length)
      try {
        localStorage.setItem(STORE + key, JSON.stringify(imgs));
      } catch {
        // the dates are asked for again next time
      }
  } catch {
    known.set(key, null);
  } finally {
    running--;
    asking.delete(key);
    waiting.shift()?.();
    listeners.forEach((f) => f());
  }
}

/** The archive images at an object's place: the list once known, null when IRSA gave none, undefined while it is being asked
 *  for (the first call asks). */
export function imageDates(name: string, ra: number, dec: number): ArchiveImage[] | null | undefined {
  if (known.has(name)) return known.get(name);
  const stored = read(name);
  if (stored) {
    known.set(name, stored);
    return stored;
  }
  if (!asking.has(name)) {
    asking.add(name);
    void ask(name, ra, dec);
  }
  return undefined;
}

/** called when the dates of some place arrive; returns the function that stops it */
export function onImageDates(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

/** The date of a backdrop at a place: the image's own date when known, else the middle of the survey; SPHEREx (and the dust map, which shows no stars) at the survey epoch. */
export function backdropYear(survey: string, imgs?: ArchiveImage[] | null, fade = 0): number {
  if (survey === 'spherex' || survey === 'dust') return SURVEY_EPOCH;
  // the star map is Gaia DR2's (epoch 2015.5) with Hipparcos and Tycho under it; zoomed past it DSS2 is faded in over it (`fade` 0..1), and the
  // object stands where it was on the DSS2 plate of its place
  if (survey === 'svs') {
    if (fade <= 0) return 2015.5;
    const plate = imgs?.find((x) => x.backdrop === 'dss2')?.year ?? BACKDROP_YEAR['dss2']!;
    return 2015.5 + Math.min(1, fade) * (plate - 2015.5);
  }
  return imgs?.find((x) => x.backdrop === survey)?.year ?? BACKDROP_YEAR[survey] ?? SURVEY_EPOCH;
}

export interface Catalogued {
  n: string;
  ra: number;
  dec: number;
  pmra: number | null;
  pmdec: number | null;
}

/** Where a catalogue object stands on the backdrop shown: its J2000 place carried by its proper motion to the backdrop's date. */
export function shownPlace(o: Catalogued, survey: string, imgs?: ArchiveImage[] | null, fade = 0): [number, number] {
  if (o.pmra == null || o.pmdec == null) return [o.ra, o.dec];
  return placeAt({ name: o.n, ra: o.ra, dec: o.dec, pmra: o.pmra, pmdec: o.pmdec }, backdropYear(survey, imgs, fade));
}
