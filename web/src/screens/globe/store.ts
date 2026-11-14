// What the globe keeps while another screen is open: the catalogue (read once per page), the coverage cells, the view and the
// choices on the side panel, so that coming back from the Lab shows the same globe and list at once.

import { outline } from '../../lib/healpix';

export interface Obj {
  n: string;
  ra: number;
  dec: number;
  k: 'bd' | 'fast' | 'mira' | 'young' | 'var';
  pmra: number | null;
  pmdec: number | null;
  sp: string;
  c: string;
  b?: number;
}

export interface CovCell {
  pts: [number, number][];
  dr1: boolean;
  t: number;
}

export type SurveyId = 'dss2' | 'spherex' | '2mass' | 'wise' | 'dust';

export const kept = {
  /** null until the first visit sets it from the aim */
  view: null as null | { ra: number; dec: number; zoom: number },
  survey: 'dss2' as SurveyId,
  proj: 'globe' as 'globe' | 'moll',
  covOn: false,
  filter: 'all',
  query: '',
  page: 1,
  per: 25,
  /** name of the selected catalogue object */
  sel: null as string | null,
};

const FIRST = ["Barnard's Star", 'WISE 0855−0714', 'ε Indi B', 'RW Aurigae', 'R Draconis'];
let catalog: Obj[] | null = null;
let catalogReq: Promise<Obj[]> | null = null;

/** The SIMBAD snapshot, test targets first; answered from memory after the first time. */
export function loadCatalog(): Promise<Obj[]> {
  if (catalog) return Promise.resolve(catalog);
  catalogReq ??= fetch('/catalog.json')
    .then((r) => r.json())
    .then((d: { catalog: Obj[] }) => {
      const rank = (o: Obj) => (o.b ? FIRST.indexOf(o.n) : FIRST.length);
      catalog = [...d.catalog].sort((x, y) => rank(x) - rank(y));
      return catalog;
    })
    .catch((e: unknown) => {
      catalogReq = null;
      throw e;
    });
  return catalogReq;
}
export const catalogNow = (): Obj[] => catalog ?? [];

let cov: CovCell[] | null = null;
let covReq: Promise<CovCell[] | null> | null = null;

/** HEALPix cells with SPHEREx exposures (the coverage layer); answered from memory after the first time. */
export function loadCoverage(): Promise<CovCell[] | null> {
  if (cov) return Promise.resolve(cov);
  covReq ??= fetch('/api/v1/coverage/map')
    .then((r) => (r.ok ? r.json() : null))
    .then((m: { order: number; releases: string[]; cells: Record<string, number[]> } | null) => {
      if (!m) {
        covReq = null;
        return null;
      }
      let max = 1;
      for (const v of Object.values(m.cells))
        max = Math.max(
          max,
          v.reduce((s, x) => s + x, 0),
        );
      const di = m.releases.indexOf('dr1');
      cov = Object.entries(m.cells).map(([k, v]) => ({
        pts: outline(m.order, Number(k), 4),
        dr1: di >= 0 && v[di]! > 0,
        t: Math.log(1 + v.reduce((s, x) => s + x, 0)) / Math.log(1 + max),
      }));
      return cov;
    })
    .catch(() => {
      covReq = null;
      return null;
    });
  return covReq;
}
export const coverageNow = (): CovCell[] | null => cov;
