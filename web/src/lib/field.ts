// The field client: ask for a field, follow its job, fetch each part once (docs/plan/05-architecture.md 5.5, 5.8).

import { parseSxp, type SxpPart } from './sxp';

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
    const r = await fetch(`/api/v1/fields?ra=${ra}&dec=${dec}&fov=${fov}`, { cache: 'no-store' });
    if (!r.ok) return this.ev.onError(`the sky service answered ${r.status}`);
    const body = (await r.json()) as { key: string; state?: string; meta?: FieldMeta; job?: JobView };
    this.key = body.key;
    if (body.state === 'done' && body.meta) {
      this.ev.onMeta(body.meta);
      this.ready = this.partsFromMeta(body.meta);
      await this.fetchParts(this.ready);
      return this.ev.onPhase('');
    }
    if (body.job) await this.follow(body.job);
  }

  private partsFromMeta(meta: FieldMeta): string[] {
    return meta.passes.flatMap((p) => ['picture', 'dets', 'bands'].map((k) => `${k}_p${p.index}`));
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
      this.ev.onPhase(view.state === 'error' ? `failed: ${view.error}` : view.phase);
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
      const r = await fetch(`/api/v1/jobs/${view.id}`, { cache: 'no-store' });
      if (r.status === 404) return this.open(...this.asked); // the service restarted: ask for the field again
      view = (await r.json()) as JobView;
    }
  }

  private async fetchParts(names: string[]): Promise<void> {
    for (const name of names) {
      if (this.parts.has(name) || ![...this.wanted].some((w) => name.startsWith(w))) continue;
      const p = fetch(`/api/v1/fields/${this.key}/parts/${name}`).then(async (r) => {
        if (!r.ok) throw new Error(`${name}: ${r.status}`);
        return parseSxp(await r.arrayBuffer());
      });
      this.parts.set(name, p);
      try {
        this.ev.onPart(name, await p);
      } catch (e) {
        this.parts.delete(name);
        this.ev.onError(String(e));
      }
    }
  }
}

export function mjdToDate(mjd: number): Date {
  return new Date((mjd - 40587) * 86400000);
}

export function monthYear(mjd: number): string {
  return mjdToDate(mjd).toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' });
}
