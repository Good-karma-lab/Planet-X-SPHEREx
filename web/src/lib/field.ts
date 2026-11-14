// The field client: ask for a field, follow its job, fetch each part once (docs/plan/05-architecture.md 5.5, 5.8).

import { parseSxp, type SxpPart } from './sxp';
import { plainPhase } from './plain';

export interface PassMeta {
  index: number;
  mjd0: number;
  mjd1: number;
  mjd_mid: number;
  detectors: Record<string, number>;
  white_balance: { kr: number; kb: number };
}

export interface BandMeta {
  key: string;
  name: string;
  label: string;
  hue: number;
}

export interface FieldMeta {
  grid: { ra0: number; dec0: number; step_arcsec: number; n: number };
  cdet: number[];
  bands: BandMeta[];
  passes: PassMeta[];
  exposures: number;
  overlapping: number;
  not_readable: number;
}

export interface JobView {
  id: string;
  state: 'queued' | 'running' | 'paused' | 'done' | 'error' | 'cancelled';
  phase: string;
  done: number;
  total: number;
  parts: string[];
  error: string | null;
}

export interface FieldEvents {
  onPhase(text: string): void;
  onPart(name: string, part: SxpPart): void;
  onMeta(meta: FieldMeta): void;
  onError(text: string): void;
}

const POLL_MS = 700;

export class FieldClient {
  private parts = new Map<string, Promise<SxpPart>>();
  private stopped = false;
  private wanted = new Set<string>(['quick_', 'picture_']);
  private ready: string[] = [];
  key: string | null = null;
  private asked: [number, number, number] = [0, 0, 0];

  constructor(private ev: FieldEvents) {}

  stop(): void {
    this.stopped = true; // leaving does not cancel the job on the service: it keeps running at a lower priority
  }

  async open(ra: number, dec: number, fov: number): Promise<void> {
    this.asked = [ra, dec, fov];
    this.ev.onPhase('asking the sky service…');
    const r = await retrying(
      `/api/v1/fields?ra=${ra}&dec=${dec}&fov=${fov}`,
      (t) => this.ev.onPhase(t),
      () => this.stopped,
    );
    if (!r) return;
    if (!r.ok) return this.ev.onError(`the sky service answered ${r.status}`);
    const body = (await r.json()) as { key: string; state?: string; meta?: FieldMeta; job?: JobView };
    this.key = body.key;
    if (body.state === 'done' && body.meta) {
      this.ev.onPhase('loading the seasons…');
      this.ev.onMeta(body.meta);
      this.ready = this.partsFromMeta(body.meta);
      await this.fetchParts(this.ready);
      return this.ev.onPhase('');
    }
    if (body.job) await this.follow(body.job);
  }

  private partsFromMeta(meta: FieldMeta): string[] {
    return [...meta.passes.flatMap((p) => ['picture', 'dets', 'bands', 'flags'].map((k) => `${k}_p${p.index}`)), 'diff'];
  }

  /** Fetch the parts whose names start with `prefix` (e.g. "dets_") from now on, and those already ready. */
  want(prefix: string): void {
    if (this.wanted.has(prefix)) return;
    this.wanted.add(prefix);
    void this.fetchParts(this.ready);
  }

  private async follow(job: JobView): Promise<void> {
    let view = job;
    while (!this.stopped) {
      this.ev.onPhase(view.state === 'error' ? `failed: ${view.error}` : plainPhase(view.phase));
      this.ready = view.parts;
      await this.fetchParts(this.ready);
      if (view.state === 'done') {
        const m = await fetch(`/api/v1/fields/${this.key}/parts/meta.json`);
        if (m.ok) this.ev.onMeta((await m.json()) as FieldMeta);
        this.ev.onPhase('');
        return;
      }
      if (view.state === 'error' || view.state === 'cancelled') return this.ev.onError(view.error ?? view.state);
      await new Promise((res) => setTimeout(res, POLL_MS));
      const r = await retrying(
        `/api/v1/jobs/${view.id}`,
        (t) => this.ev.onPhase(t),
        () => this.stopped,
      );
      if (!r) return;
      if (r.status === 404) return this.open(...this.asked); // the service restarted: ask for the field again
      if (!r.ok) continue;
      view = (await r.json()) as JobView;
    }
  }

  /** Fetch every wanted part not held yet, all at once; each is handed over as soon as it arrives. */
  private async fetchParts(names: string[]): Promise<void> {
    const todo = names.filter((name) => !this.parts.has(name) && [...this.wanted].some((w) => name.startsWith(w)));
    await Promise.all(
      todo.map(async (name) => {
        const p = (async () => {
          for (let attempt = 0; ; attempt++) {
            const r = await fetch(`/api/v1/fields/${this.key}/parts/${name}`);
            if (r.ok) return parseSxp(await r.arrayBuffer());
            if (attempt >= 4 || (r.status !== 502 && r.status !== 503 && r.status !== 504)) throw new Error(`${name}: ${r.status}`);
            await new Promise((res) => setTimeout(res, 1000 * (attempt + 1)));
          }
        })();
        this.parts.set(name, p);
        try {
          this.ev.onPart(name, await p);
        } catch (e) {
          this.parts.delete(name);
          if (!name.startsWith('flags_')) this.ev.onError(String(e)); // a pass without flagged pixels has no flags part
        }
      }),
    );
  }
}

/** A request that waits out a restart of the service (502/503/504 or no answer): retried with a growing pause for about a minute.
 *  Null when the caller stopped meanwhile. */
async function retrying(url: string, say: (t: string) => void, stopped: () => boolean): Promise<Response | null> {
  for (let attempt = 0; ; attempt++) {
    if (stopped()) return null;
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if ((r.status !== 502 && r.status !== 503 && r.status !== 504) || attempt >= 12) return r;
    } catch (e) {
      if (attempt >= 12) throw e;
    }
    say('the sky service is restarting; trying again…');
    await new Promise((res) => setTimeout(res, Math.min(8000, 1000 * (attempt + 1))));
  }
}

export function mjdToDate(mjd: number): Date {
  return new Date((mjd - 40587) * 86400000);
}

export function monthYear(mjd: number): string {
  return mjdToDate(mjd).toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' });
}
