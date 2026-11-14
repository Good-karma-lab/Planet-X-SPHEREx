import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { runJob, progressText, useProduct } from './jobs';
import './search.css';

export interface DistantCandidate {
  sn: number;
  d: number;
  d_lo: number;
  d_hi: number;
  mu: [number, number];
  ra: number;
  dec: number;
  flux_mjy?: number;
  err_mjy?: number;
  phot_sn?: number | null;
  ok?: boolean;
  control_sn?: number[];
}

export interface DistantSlider {
  n: number;
  step: number;
  distances: number[];
  /** per distance, per visit: the shift in grid pixels */
  offsets_px: [number, number][][];
  /** own motion limit per distance, arcsec/day */
  mu_max: number[];
  /** per visit: the time from the reference (days) and the noise of its difference */
  dt: number[];
  sg: number[];
  t: number[];
}

/** The one-pass result of the search for bodies beyond Neptune. */
export interface DistantResult {
  status: string;
  pass: number;
  n_visits: number;
  n_hypotheses: number;
  dmin?: number;
  dmin_used?: number;
  dmax?: number;
  scan_secs: number;
  threshold: number;
  null?: number[];
  notes?: string[];
  passes: { index: number; n: number; mjd0: number }[];
  candidates: DistantCandidate[];
  control: { measured: number; reach_sn5: number };
  slider: DistantSlider;
  bin_url: string;
}

export type DistantState =
  | { state: 'idle' }
  | { state: 'loading'; text: string; frac: number | null }
  | { state: 'none'; text: string }
  | { state: 'error'; text: string }
  | { state: 'ok'; key: string; r: DistantResult; bin: Uint8Array; di: number; mu: [number, number]; neg: boolean; sel: number | null };

export interface DistantAim {
  ra: number;
  dec: number;
  fov: number;
  /** the pass to search: its index, or null for the pass of this MJD */
  pass?: number | null;
  mjd?: number;
}

/** What the sliders hold, for the address: distance index, own motion in percent of the limit, the noise view. */
export interface DistantSliders {
  di: number;
  mu: [number, number];
  neg: boolean;
}

export const DMIN = 30;
export const DMAX = 300;

/**
 * The search for bodies beyond Neptune on one pass of a field (a job of the service: it reads every exposure, minutes the first time). The result is the sum of the
 * differences of the visits; its sliders (distance, own motion) shift the visits and add them again in the page, so a point comes into focus as one drags.
 * `available` is false while the service does not make this product, so nothing is shown. `restore`: slider values to start from (from the address).
 */
export function useDistant(aim: DistantAim, restore?: DistantSliders | null) {
  const available = useProduct('distant');
  const [ds, setDs] = useState<DistantState>({ state: 'idle' });
  const [peak, setPeak] = useState<number | null>(null);
  /** the finished one-pass results of this field by pass index: the keys the link of the passes asks for */
  const [keys, setKeys] = useState<Record<number, string>>({});
  const tok = useRef(0);
  const keep = useRef(restore);
  keep.current = restore;
  // a new field or aim: the old result no longer belongs to the picture
  useEffect(() => {
    tok.current++;
    setDs({ state: 'idle' });
    setKeys({});
  }, [aim.ra, aim.dec, aim.fov]);
  const start = async (pass: number | null) => {
    const my = ++tok.current;
    const t0 = Date.now();
    setDs({ state: 'loading', text: 'asking the service…', frac: null });
    const q = new URLSearchParams({
      ra: String(aim.ra),
      dec: String(aim.dec),
      fov: String(aim.fov >= 20 ? 18 : aim.fov),
      dmin: String(DMIN),
      dmax: String(DMAX),
    });
    if (pass != null) q.set('pass', String(pass));
    else if (aim.mjd != null) q.set('mjd', String(aim.mjd));
    try {
      const j = await runJob(`/api/v1/distant?${q}`, {
        cancelled: () => my !== tok.current,
        onJob: (jj) => {
          const p = progressText(jj, t0);
          if (my === tok.current) setDs({ state: 'loading', text: p.text, frac: p.frac });
        },
      });
      if (my !== tok.current) return;
      if (!j) return setDs({ state: 'none', text: 'The service does not make this search yet.' });
      const r = j.result as unknown as DistantResult;
      if (!r || r.status !== 'ok') return setDs({ state: 'none', text: (r?.notes ?? []).join(' ') || 'No result for this field.' });
      const bin = new Uint8Array(await (await fetch(r.bin_url)).arrayBuffer());
      const a = keep.current;
      const nd = r.slider.distances.length;
      const key = String(j.key ?? '');
      if (key) setKeys((k) => ({ ...k, [r.pass]: key }));
      setDs({
        state: 'ok',
        key,
        r,
        bin,
        di: a ? Math.max(0, Math.min(nd - 1, a.di)) : Math.min(nd - 1, 24),
        mu: a ? [clamp100(a.mu[0]), clamp100(a.mu[1])] : [0, 0],
        neg: !!a?.neg,
        sel: null,
      });
    } catch (e) {
      if ((e as Error).message === 'cancelled') return;
      setDs({ state: 'error', text: (e as Error).message });
    }
  };
  const patch = (p: Partial<{ di: number; mu: [number, number]; neg: boolean; sel: number | null }>) =>
    setDs((s) => (s.state === 'ok' ? { ...s, ...p } : s));
  return {
    available,
    ds,
    /** the brightest point of the shifted sum on show (set by DistantCanvas) */
    peak,
    setPeak,
    keys,
    start,
    close: () => {
      tok.current++;
      setDs({ state: 'idle' });
    },
    setDi: (di: number) => patch({ di }),
    setMu: (mu: [number, number]) => patch({ mu }),
    setNeg: (neg: boolean) => patch({ neg }),
    /** put the sliders on a candidate (the nearest distance, its own motion) */
    select: (i: number) =>
      setDs((s) => {
        if (s.state !== 'ok') return s;
        const c = s.r.candidates[i]!;
        const dd = s.r.slider.distances;
        let bi = 0;
        dd.forEach((d, k) => {
          if (Math.abs(Math.log(d / c.d)) < Math.abs(Math.log(dd[bi]! / c.d))) bi = k;
        });
        const mm = s.r.slider.mu_max[bi]!;
        return { ...s, di: bi, mu: [Math.round((c.mu[0] / mm) * 100), Math.round((c.mu[1] / mm) * 100)], sel: i };
      }),
  };
}

const clamp100 = (v: number) => Math.max(-100, Math.min(100, v));

export type Distant = ReturnType<typeof useDistant>;

/** The reading under the sliders: distance, own motion, and the brightest point in view. */
export function readText(s: Extract<DistantState, { state: 'ok' }>, peak: number | null): string {
  const sl = s.r.slider;
  const d = sl.distances[s.di]!;
  const mm = sl.mu_max[s.di]!;
  const mu = [(s.mu[0] / 100) * mm, (s.mu[1] / 100) * mm];
  return `distance ${d.toFixed(0)} au · own motion ${mu[0]!.toFixed(2)}, ${mu[1]!.toFixed(2)} ″/day (limit ${mm.toFixed(2)})${peak != null ? ` · brightest point in view: S/N ${peak.toFixed(1)}` : ''}`;
}

/** The shifted sum on a canvas that fills its parent (the owner lays it over the picture): drag a slider and a point comes into focus. */
export function DistantCanvas({ d }: { d: Distant }) {
  const s = d.ds;
  const ref = useRef<HTMLCanvasElement>(null);
  const off = useRef<HTMLCanvasElement | null>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || s.state !== 'ok') return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = Math.round(cv.clientWidth);
    const H = Math.round(cv.clientHeight);
    if (cv.width !== W * dpr) {
      cv.width = W * dpr;
      cv.height = H * dpr;
    }
    const sl = s.r.slider;
    const n = sl.n;
    const V = sl.t.length;
    const nn = n * n;
    const sh = sl.offsets_px[s.di]!;
    const mm = sl.mu_max[s.di]!;
    const mu = [((s.mu[0] / 100) * mm) / sl.step, ((s.mu[1] / 100) * mm) / sl.step];
    const out = new Float32Array(nn);
    const den = new Float32Array(nn);
    const sgn = s.neg ? -1 : 1;
    for (let i = 0; i < V; i++) {
      const om = 1 / sl.sg[i]!;
      const sx = Math.round(sh[i]![0] + mu[0]! * sl.dt[i]!);
      const sy = Math.round(sh[i]![1] + mu[1]! * sl.dt[i]!);
      const base = i * nn;
      if (Math.abs(sx) >= n || Math.abs(sy) >= n) continue;
      const y0 = Math.max(0, -sy);
      const y1 = n - Math.max(0, sy);
      const x0 = Math.max(0, -sx);
      const x1 = n - Math.max(0, sx);
      for (let y = y0; y < y1; y++) {
        let o = y * n + x0;
        let q = base + (y + sy) * n + x0 + sx;
        for (let x = x0; x < x1; x++, o++, q++) {
          const v = s.bin[q]!;
          if (v) {
            out[o]! += sgn * om * ((v - 128) / 25);
            den[o]! += om * om;
          }
        }
      }
    }
    const sn = new Float32Array(nn);
    const samp: number[] = [];
    let wmax = 0;
    for (let p = 0; p < nn; p++) if (den[p]! > wmax) wmax = den[p]!;
    for (let p = 0; p < nn; p++)
      if (den[p]! >= 0.6 * wmax) {
        sn[p] = out[p]! / Math.sqrt(den[p]!);
        if (p % 9 === 0) samp.push(sn[p]!);
      }
    samp.sort((a, b) => a - b);
    const md = samp[samp.length >> 1] ?? 0;
    const ad = samp.map((x) => Math.abs(x - md)).sort((a, b) => a - b);
    const spread = Math.max(1.4826 * (ad[ad.length >> 1] ?? 1), 0.5);
    const oc = off.current ?? (off.current = document.createElement('canvas'));
    oc.width = oc.height = n;
    const c0 = oc.getContext('2d')!;
    const id = c0.createImageData(n, n);
    let peak = 0;
    for (let p = 0; p < nn; p++) {
      const o4 = ((n - 1 - Math.floor(p / n)) * n + (p % n)) * 4;
      if (den[p]! < 0.6 * wmax) {
        id.data.set([6, 12, 16, 255], o4);
        continue;
      }
      const z = (sn[p]! - md) / spread;
      if (z > peak) peak = z;
      const t = Math.max(0, Math.min(1, z / 8));
      id.data.set([14 + 241 * t, 18 + 170 * t * t, 24 + 40 * t * t * t, 255], o4);
    }
    c0.putImageData(id, 0, 0);
    const c = cv.getContext('2d')!;
    c.imageSmoothingEnabled = false;
    c.drawImage(oc, 0, 0, cv.width, cv.height);
    c.font = `600 ${12 * dpr}px ui-sans-serif, system-ui, sans-serif`;
    c.lineWidth = 3 * dpr;
    c.strokeStyle = 'rgba(4,9,13,.9)';
    c.fillStyle = '#e6f3f7';
    const t1 = `${s.neg ? 'noise · ' : ''}pass ${s.r.pass + 1} · ${sl.distances[s.di]!.toFixed(0)} au · peak S/N ${peak.toFixed(1)}`;
    c.strokeText(t1, 10 * dpr, cv.height - 12 * dpr);
    c.fillText(t1, 10 * dpr, cv.height - 12 * dpr);
    d.setPeak(peak);
  }, [s.state === 'ok' ? s.r : null, s.state === 'ok' ? s.bin : null, s.state === 'ok' ? s.di : 0, s.state === 'ok' ? s.mu[0] : 0, s.state === 'ok' ? s.mu[1] : 0, s.state === 'ok' && s.neg]);
  return <canvas ref={ref} class="ds-canvas" aria-label="The sum of the differences shifted for the chosen distance" />;
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;
const utDate = (m: number) => new Date((m - 40587) * 864e5).toISOString().slice(0, 10);

/**
 * The side panel of the search beyond Neptune: the button (with what it reads and how long it takes), the progress of the job, the pass, the three sliders, the
 * status lines (what the noise reaches, what is above the bar, how far a blind search reaches) and the peaks. Nothing is marked unless a peak is above the bar.
 */
export function DistantPanel({
  d,
  currentPass,
  passLabel,
  onPick,
}: {
  d: Distant;
  /** the pass on show in the picture (0-based) and its label, for the button */
  currentPass: number;
  passLabel?: string;
  /** a peak was chosen: the owner opens its card (see distantFind) */
  onPick?: (c: DistantCandidate, i: number, r: DistantResult) => void;
}) {
  const { ds } = d;
  const reading = useMemo(() => (ds.state === 'ok' ? readText(ds, d.peak) : ''), [ds, d.peak]);
  if (d.available !== true) return null;
  if (ds.state === 'idle')
    return (
      <div class="dsbox">
        <div class="srrow" title="Too faint for any single frame, a body this far out shows only when the visits of a pass are added along its motion. Drag a distance slider and watch a point come into focus. Reads every exposure of this field (D1–D3, all passes): a few minutes the first time.">
          <span class="srt">Find Planet X</span>
          <button class="btn sm" onClick={() => void d.start(null)}>
            Search
          </button>
        </div>
        <div class="mono-s">searches the pass on show: pass {currentPass + 1}{passLabel ? ` · ${passLabel}` : ''} · distance {DMIN}–{DMAX} au · a few minutes the first time</div>
      </div>
    );
  return (
    <div class="dsbox">
      <div class="sechead">
        <span class="search-h">Find Planet X</span>
        <span class="mono-s">{ds.state === 'ok' ? `pass ${ds.r.pass + 1} · ${ds.r.n_visits} visits` : ''}</span>
      </div>
      {ds.state === 'loading' && (
        <>
          <div class="mono-s">{ds.text}</div>
          <div class={`ds-prog${ds.frac == null ? ' indet' : ''}`}>
            <i style={ds.frac == null ? '' : `width:${Math.round(ds.frac * 100)}%`} />
          </div>
          <button class="btn sm" onClick={d.close}>
            Cancel
          </button>
        </>
      )}
      {(ds.state === 'none' || ds.state === 'error') && (
        <>
          <div class="mono-s">{ds.state === 'none' ? ds.text : `The search failed: ${ds.text}`}</div>
          <button class="btn sm" onClick={d.close}>
            Close
          </button>
        </>
      )}
      {ds.state === 'ok' && (
        <>
          <div class="chips">
            {ds.r.passes.map((p) => (
              <button key={p.index} class={`chip${p.index === ds.r.pass ? ' on' : ''}`} title={`${p.n} exposures`} onClick={() => void d.start(p.index)}>
                pass {p.index + 1} · {utDate(p.mjd0)}
              </button>
            ))}
          </div>
          <div class="mono-s">{reading}</div>
          <Slider label="Distance" min={0} max={ds.r.slider.distances.length - 1} value={ds.di} onInput={d.setDi} />
          <Slider label="Own motion, east–west" min={-100} max={100} value={ds.mu[0]} onInput={(v) => d.setMu([v, ds.mu[1]])} />
          <Slider label="Own motion, north–south" min={-100} max={100} value={ds.mu[1]} onInput={(v) => d.setMu([ds.mu[0], v])} />
          <label class="ds-chk">
            <input type="checkbox" checked={ds.neg} onChange={(e) => d.setNeg((e.target as HTMLInputElement).checked)} /> Show the noise: the same sum of the negated differences
          </label>
          <DistantLines r={ds.r} />
          {ds.r.candidates.map((c, i) => (
            <button
              key={i}
              class={`btn sm leadbtn${ds.sel === i ? ' on' : ''}`}
              onClick={() => {
                d.select(i);
                onPick?.(c, i, ds.r);
              }}
            >
              <b>Peak {i + 1}</b> · S/N {c.sn} · {c.d} au ({c.d_lo}–{c.d_hi}) · photometry {c.phot_sn != null ? `${c.phot_sn}σ` : 'n/a'}
              {c.ok ? ' · confirmed' : ''}
            </button>
          ))}
        </>
      )}
    </div>
  );
}

function Slider({ label, min, max, value, onInput }: { label: string; min: number; max: number; value: number; onInput: (v: number) => void }) {
  return (
    <>
      <label class="ds-lab">{label}</label>
      <input type="range" min={min} max={max} step={1} value={value} aria-label={label} onInput={(e) => onInput(Number((e.currentTarget as HTMLInputElement).value))} />
    </>
  );
}

/** The service's notes with the same note counted once ("86 notes" of one kind is "an exposure was not read ×86"), at most four kinds. */
export function noteText(notes: string[]): string {
  const m = new Map<string, number>();
  for (const n of notes) {
    const k = n.replace(/\s*\(.*?\)\s*$/, '').trim();
    m.set(k, (m.get(k) ?? 0) + 1);
  }
  const all = [...m.entries()].sort((a, b) => b[1] - a[1]);
  return all.slice(0, 4).map(([k, n]) => (n > 1 ? `${k} ×${n}` : k)).join(' · ') + (all.length > 4 ? ` · and ${all.length - 4} more kinds` : '');
}

/** The status lines of a result, in the words of the search: what it did, what the noise reaches, how many peaks, how far a blind search reaches. */
export function DistantLines({ r }: { r: DistantResult }) {
  const nl = r.null?.[0];
  const L = (t: string, tip?: string) => (
    <div class="mono-s stl" title={tip}>
      {t}
    </div>
  );
  return (
    <>
      {L(`${r.n_hypotheses.toLocaleString('en-US')} orbits · ${r.dmin_used ?? r.dmin ?? DMIN}–${r.dmax ?? DMAX} au · ${r.scan_secs} s`, 'Hypotheses of distance and own motion scanned for this pass.')}
      {L(`noise reaches S/N ${nl != null ? nl.toFixed(1) : '?'} · bar ${r.threshold.toFixed(1)}`, 'The same sum of the negated differences gives the noise ceiling; a peak counts above the bar.')}
      {L(r.candidates.length ? `${plural(r.candidates.length, 'peak')} above the bar · a lead, not a discovery` : 'nothing above the bar · nothing marked')}
      {L(`${r.control.measured} control tracks · ${r.control.reach_sn5} reach S/N 5`, 'Photometry on the exposures along the track and on control tracks aside.')}
      {L('blind limit about V 20–21 · reflected light', 'One blind search of this kind reaches about V 20–21; a Planet Nine of the predicted brightness is fainter.')}
      {L('Planet Nine: margin 1.4–1.7× only in D4 (3 µm)', 'The one place SPHEREx adds to the existing Planet Nine searches is 3 µm (D4); the margin over the WISE search is small.')}
      {r.notes && r.notes.length > 0 && <div class="mono-s">{noteText(r.notes)}</div>}
    </>
  );
}

/** The rows of the card of a peak (the owner shows them in its find card). */
export function peakRows(c: DistantCandidate, r: DistantResult): [string, string, string][] {
  return [
    ['Signal', `S/N ${c.sn} in the sum · ${c.phot_sn != null ? `${c.phot_sn} σ` : 'n/a'} in photometry`, 'the sum over the visits along one orbit, and PSF photometry along it'],
    ['Bar and noise', `bar ${r.threshold.toFixed(1)} · noise reaches ${r.null?.[0]?.toFixed(1) ?? '?'}`, 'the highest peak of the negated sum'],
    ['Distance', `${c.d} au (${c.d_lo}–${c.d_hi})`, 'of the best orbit, and the range the knob keeps within 10 % of the peak'],
    ['Own motion', `${c.mu[0]}, ${c.mu[1]} ″/day`, 'east, north'],
    ['Control tracks', c.control_sn?.length ? `${c.control_sn.join(', ')} σ` : 'n/a', 'tracks beside the peak'],
    ['Reach', 'a blind search reaches about V 20–21; Planet Nine margin 1.4–1.7× only in D4', 'in reflected light'],
  ];
}
