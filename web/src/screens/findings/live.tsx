import { useEffect, useState } from 'preact/hooks';

export const FIELD = 20;

export interface CatObj {
  n: string;
  ra: number;
  dec: number;
  pmra: number | null;
  pmdec: number | null;
  b?: number;
}
export interface TrackLite {
  speed: number;
  pa: number;
  passes: number[];
  check: string;
  gaia: { source_id: string; pm: number; g?: number | null } | null;
}
export interface AnomalyLite {
  n: number;
  score: number;
  drivers: { lo: number; hi: number; z: number; share: number }[];
  identified?: { simbad?: { state: string; name?: string; type?: string }; gaia?: { state: string; source_id?: string } };
}
export interface Live {
  name: string;
  ra: number;
  dec: number;
  movers: { state: string; result?: { field?: string; passes: number; sources_per_pass: number[]; tracks: TrackLite[]; pairs?: unknown[]; chance?: { trials: number; mean: number; any: number } } } | null;
  anomalies: {
    state: string;
    result?: { with_spectrum: number; threshold: number | null; anomalies: AnomalyLite[]; reason: string } | null;
  } | null;
}


/** What the searches found in the 20′ fields of the five test targets, read live from the service (nothing is started here). */
export function useLive(): Live[] {
  const [live, setLive] = useState<Live[]>([]);
  useEffect(() => {
    let stop = false;
    void (async () => {
      const cat = (await fetch('/catalog.json').then((r) => r.json())) as { catalog: CatObj[] };
      const targets = cat.catalog.filter((o) => o.b);
      const out: Live[] = [];
      for (const o of targets) {
        const dt = 2025.9 - 2000;
        const dec = o.dec + ((o.pmdec ?? 0) * dt) / 3.6e6;
        const ra = (o.ra + ((o.pmra ?? 0) * dt) / 3.6e6 / Math.cos((((dec + o.dec) / 2) * Math.PI) / 180) + 360) % 360;
        const q = `ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&fov=${FIELD}`;
        const [mv, an] = await Promise.all([
          fetch(`/api/v1/movers?${q}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null),
          fetch(`/api/v1/anomalies?${q}`)
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null),
        ]);
        out.push({ name: o.n, ra, dec, movers: mv, anomalies: an });
        if (stop) return;
        setLive([...out]);
      }
    })();
    return () => {
      stop = true;
    };
  }, []);
  return live;
}

