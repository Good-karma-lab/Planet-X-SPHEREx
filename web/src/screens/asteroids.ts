// The asteroid search of the pass on show, started by itself when a field opens: the visits of the pass are built (the film), then the
// service looks for unknown movers in them (straight-line tracks through the candidates of every visit, with a chance control).

import { useEffect, useState } from 'preact/hooks';
import type { FilmControl, FilmTrack } from './search';
import { plainPhase } from '../lib/plain';

export interface AsteroidSearch {
  state: 'idle' | 'building' | 'searching' | 'done' | 'none' | 'failed';
  text: string;
  /** 0..1 when the service says how far it is */
  frac: number | null;
  filmKey?: string;
  tracks?: FilmTrack[];
  control?: FilmControl | null;
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

async function getJson(url: string): Promise<{ status: number; body: Record<string, unknown> | null }> {
  try {
    const r = await fetch(url, { cache: 'no-store' });
    return { status: r.status, body: r.ok ? ((await r.json()) as Record<string, unknown>) : null };
  } catch {
    return { status: 0, body: null };
  }
}

export function useAsteroidSearch(ra: number, dec: number, fov: number, mjd: number | undefined, enabled: boolean): AsteroidSearch {
  const [s, setS] = useState<AsteroidSearch>({ state: 'idle', text: '', frac: null });
  const mjdKey = mjd ? mjd.toFixed(1) : '';
  useEffect(() => {
    setS({ state: 'idle', text: '', frac: null });
    if (!enabled || !mjd) return;
    let stop = false;
    void (async () => {
      setS({ state: 'building', text: 'building the visits of the pass…', frac: null });
      let key = '';
      for (let i = 0; i < 2000 && !stop; i++) {
        const { status, body } = await getJson(`/api/v1/film?ra=${ra}&dec=${dec}&fov=${fov}&mjd=${mjdKey}&bg=1`);
        if (stop) return;
        if (!body) {
          if (status !== 503 && status !== 502 && status !== 429 && status !== 0)
            return setS({ state: 'failed', text: `the service answered ${status}`, frac: null });
          await sleep(3000);
          continue;
        }
        if (body['state'] === 'none')
          return setS({ state: 'none', text: String(body['note'] ?? 'this pass has too few visits'), frac: null });
        if (body['state'] === 'done') {
          key = String(body['key']);
          break;
        }
        const job = body['job'] as { id: string } | undefined;
        if (job) {
          const j = await getJson(`/api/v1/jobs/${job.id}`);
          const v = j.body as { state?: string; phase?: string; done?: number; total?: number; error?: string } | null;
          if (v?.state === 'error') return setS({ state: 'failed', text: v.error ?? 'the visits could not be built', frac: null });
          if (v)
            setS({
              state: 'building',
              text: `building the visits: ${plainPhase(v.phase)}`,
              frac: v.total ? (v.done ?? 0) / v.total : null,
            });
        }
        await sleep(2500);
      }
      if (!key || stop) return;
      for (let i = 0; i < 2000 && !stop; i++) {
        const { status, body } = await getJson(`/api/v1/film/${key}/tracks`);
        if (stop) return;
        if (status === 404) return setS({ state: 'failed', text: 'this service has no asteroid search', frac: null, filmKey: key });
        if (body) {
          if (body['state'] === 'done')
            return setS({
              state: 'done',
              text: '',
              frac: 1,
              filmKey: key,
              tracks: (body['tracks'] as FilmTrack[]) ?? [],
              control: (body['control'] as FilmControl | null) ?? null,
            });
          if (body['state'] === 'error')
            return setS({ state: 'failed', text: String(body['error'] ?? 'the search failed'), frac: null, filmKey: key });
          const done = Number(body['done'] ?? 0);
          const total = Number(body['total'] ?? 0);
          setS({
            state: 'searching',
            text: String(body['phase'] ?? 'searching the visits'),
            frac: total ? done / total : null,
            filmKey: key,
          });
        }
        await sleep(2500);
      }
    })();
    return () => {
      stop = true;
    };
  }, [ra, dec, fov, mjdKey, enabled]);
  return s;
}
