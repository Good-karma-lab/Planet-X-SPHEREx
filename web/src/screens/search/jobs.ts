import { useEffect, useState } from 'preact/hooks';
import { plainPhase } from '../../lib/plain';

/** A long job of the service, as every search route answers it: state (or status), the phase, progress, and the result when done. */
export interface JobView {
  state?: string;
  status?: string;
  phase?: string;
  done?: number;
  total?: number;
  error?: string | null;
  /** the service's own estimate of the whole job, seconds (when it has one) */
  estimate_s?: number;
  result?: Record<string, unknown>;
  [k: string]: unknown;
}

export const stateOf = (j: JobView) => j.state ?? j.status ?? '';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Asks a route again and again until the job is done; `onJob` sees every answer (progress). 404: the route does not exist (returns null). */
export async function runJob(
  url: string,
  hooks: { cancelled: () => boolean; onJob?: (j: JobView) => void; every?: number; max?: number; bg?: boolean },
): Promise<JobView | null> {
  // every run asks no oftener than every 5 s; an automatic run of the page (bg) also waits while the tab is hidden before its first ask
  for (let i = 0; i < (hooks.max ?? 2000); i++) {
    if (hooks.cancelled()) throw new Error('cancelled');
    while (hooks.bg && typeof document !== 'undefined' && document.hidden) {
      await sleep(1000);
      if (hooks.cancelled()) throw new Error('cancelled');
    }
    const r = await fetch(url, { cache: 'no-store' });
    if (r.status === 404) return null;
    if (r.status === 429) {
      await sleep(5000);
      continue;
    }
    if (!r.ok) throw new Error(`service answered ${r.status}`);
    const j = (await r.json()) as JobView;
    hooks.onJob?.(j);
    const s = stateOf(j);
    if (s === 'error' || s === 'cancelled') throw new Error(j.error || 'the search failed');
    if (s === 'done' || s === 'ok') return j;
    // a running search is asked again every 5 s at most often, and not at all while the tab is hidden (it keeps running on the service)
    await sleep(Math.max(5000, hooks.every ?? 5000));
    while (typeof document !== 'undefined' && document.hidden && !hooks.cancelled()) await sleep(2000);
  }
  throw new Error('the search took too long');
}

/** "about a minute", "about 12 minutes", "about 1.5 hours" */
export const about = (s: number) =>
  s < 90 ? 'about a minute' : s < 5400 ? `about ${Math.round(s / 60)} minutes` : `about ${(s / 3600).toFixed(1)} hours`;

/** The progress line of a running job: the phase, "n of m", the time so far and the time left (the service's estimate, else from the rate). */
/** the jobs (by their start time) whose service has said its own time left */
const ownLeft = new Set<number>();

export function progressText(j: JobView | null, t0: number, head = ''): { text: string; frac: number | null } {
  const el = (Date.now() - t0) / 1000;
  const frac = j && j.total ? Math.min(0.99, (j.done ?? 0) / j.total) : null;
  let left = '';
  // the service's own words win when its phase already says the time left (the scan blocks do); once it has, the up-front estimate is
  // never shown again for this job (it was a guess the job has since corrected): the phases after it say finishing
  const own = /\bleft\b/.test(j?.phase ?? '');
  if (own) ownLeft.add(t0);
  if (own) left = '';
  else if (ownLeft.has(t0)) left = ' · finishing';
  else if (j?.estimate_s && j.estimate_s > el) left = ` · ${about(j.estimate_s - el).replace('about ', '~')} left`;
  else if (j?.estimate_s) left = ' · finishing';
  else if (frac != null && frac > 0.05 && el > 20) left = ` · ${about((el * (1 - frac)) / frac).replace('about ', '~')} left`;
  // the time so far moves every second it is shown, so it is never read as a time left that is stuck
  const sofar =
    el >= 60
      ? ` · ${Math.floor(el / 60)} min ${String(Math.floor(el % 60)).padStart(2, '0')} s so far`
      : el >= 5
        ? ` · ${Math.round(el)} s so far`
        : '';
  return { text: `${head}${plainPhase(j?.phase) || 'working'}${j?.total ? ` · ${j.done ?? 0} of ${j.total}` : ''}${sofar}${left}`, frac };
}

/** Is a product of the service there at all? (/api/v1/health lists the products it makes; nothing is asked of a search route just to find out.) null while unknown. */
export function useProduct(name: string): boolean | null {
  const [has, setHas] = useState<boolean | null>(null);
  useEffect(() => {
    let stop = false;
    fetch('/api/v1/health', { cache: 'no-store' })
      .then((r) => r.json())
      .then((h: { product_versions?: Record<string, string> }) => {
        if (!stop) setHas(!!h.product_versions && name in h.product_versions);
      })
      .catch(() => {
        if (!stop) setHas(false);
      });
    return () => {
      stop = true;
    };
  }, [name]);
  return has;
}

/** The service's honest words: one string, or an object of strings (limit, planet_nine, lead, ...), as lines. */
export function honestLines(h: unknown): string[] {
  if (typeof h === 'string') return [h];
  if (h && typeof h === 'object') return Object.values(h as Record<string, unknown>).filter((v): v is string => typeof v === 'string');
  return [];
}
