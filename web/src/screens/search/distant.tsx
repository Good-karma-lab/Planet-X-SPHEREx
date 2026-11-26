import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { about, runJob, progressText, useProduct } from './jobs';
import './search.css';

/** JPL's identification of a lead (service/fpx_known.py): a body that stands where the lead stands in every pass, at its distance. */
export interface KnownBody {
  state: 'ok' | 'none' | 'pending' | 'unavailable';
  name?: string;
  words?: string;
  seps?: number[];
  V?: number | null;
  r_au?: number | null;
}
/** "Sedna (JPL), found blind" from the identification, or null. */
export function knownName(k: KnownBody | undefined): string | null {
  if (k?.state !== 'ok' || !k.name) return null;
  const short = k.name.split('(')[0]!.trim().replace(/^\d+\s+/, '');
  return `${short} (JPL), found blind`;
}

export interface DistantCandidate {
  known?: KnownBody;
  /** the distance is not bounded: the S/N is still high at the edge of the range searched */
  edge?: boolean;
  /** why a peak above the bar is not a lead: a bright star's halo, or a signal that grows to the edge of the searched distances (a static source) */
  residual?: string;
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
  /** ok, or one-pass (the field has fewer than two passes: the one-pass result is all there is) */
  status: string;
  pass: number;
  n_visits: number;
  n_hypotheses: number;
  dmin?: number;
  dmin_used?: number;
  dmax?: number;
  /** the parameters the search ran with (its distance range) */
  params?: { dmin?: number; dmax?: number };
  /** the time the visits of the pass span */
  span_days?: number;
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
  /** a distance range and detectors other than the defaults (from a link: the Planet X page's "Run Find Planet X") */
  dmin?: number;
  dmax?: number;
  det?: string;
}

/** What the sliders hold, for the address: distance index, own motion in percent of the limit, the noise view. */
export interface DistantSliders {
  di: number;
  mu: [number, number];
  neg: boolean;
}

/** A lead is a peak the photometry along its orbit confirms (ok): the one rule Findings counts too. Peaks that only stand above the bar are not leads. */
export const peaksAbove = (ds: DistantState): number => (ds.state === 'ok' ? ds.r.candidates.filter((c) => c.ok).length : 0);

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
  const closedByUser = useRef(false);
  const keep = useRef(restore);
  keep.current = restore;
  // a new field or aim: the old result no longer belongs to the picture; leaving the page (or the field) stops asking about it, so the
  // service drops a background search nobody follows (26 Nov: it was asked every 5 s from About and the Cabinet until it ended)
  useEffect(() => {
    tok.current++;
    closedByUser.current = false;
    setDs({ state: 'idle' });
    setKeys({});
    return () => {
      tok.current++;
    };
  }, [aim.ra, aim.dec, aim.fov]);
  /** `bg`: an automatic run of the page, which gives way to every person's job (the service's bg=1) */
  const start = async (pass: number | null, bg = false) => {
    const my = ++tok.current;
    const t0 = Date.now();
    setDs({ state: 'loading', text: 'asking the service…', frac: null });
    const q = new URLSearchParams({
      ra: String(aim.ra),
      dec: String(aim.dec),
      fov: String(aim.fov >= 20 ? 18 : aim.fov),
      dmin: String(aim.dmin ?? DMIN),
      dmax: String(aim.dmax ?? DMAX),
    });
    if (aim.det) q.set('det', aim.det);
    if (bg) q.set('bg', '1');
    if (pass != null) q.set('pass', String(pass));
    else if (aim.mjd != null) q.set('mjd', String(aim.mjd));
    try {
      const j = await runJob(`/api/v1/distant?${q}`, {
        bg,
        cancelled: () => my !== tok.current,
        onJob: (jj) => {
          const p = progressText(jj, t0);
          if (my === tok.current) setDs({ state: 'loading', text: p.text, frac: p.frac });
        },
      });
      if (my !== tok.current) return;
      if (!j) return setDs({ state: 'none', text: 'The service does not make this search yet.' });
      const r = j.result as unknown as DistantResult;
      if (r?.status === 'no-coverage') return setDs({ state: 'none', text: `No SPHEREx exposure of ${aim.det && aim.det !== '123' ? `detector${aim.det.length > 1 ? 's' : ''} D${aim.det.split('').join(', D')}` : 'detectors D1–D3'} covers this field.` });
      if (!r || (r.status !== 'ok' && r.status !== 'one-pass'))
        return setDs({ state: 'none', text: (r?.notes ?? []).join(' ') || 'No result for this field.' });
      const binUrl = r.bin_url || `/api/v1/distant/${String(j.key)}/bin`;
      const br = await fetch(binUrl);
      if (!br.ok) throw new Error(`the sum of the differences could not be read (${br.status})`);
      const bin = new Uint8Array(await br.arrayBuffer());
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
    /** the distance range the search asks for (the defaults, or the one a link set) */
    range: { dmin: aim.dmin ?? DMIN, dmax: aim.dmax ?? DMAX, det: aim.det ?? '123' },
    /** the brightest point of the shifted sum on show (set by DistantCanvas) */
    peak,
    setPeak,
    keys,
    start,
    close: () => {
      tok.current++;
      // a person's cancel stays a cancel: nothing starts this search again on this field (an automatic run asks `closed()`)
      closedByUser.current = true;
      setDs({ state: 'idle' });
    },
    closed: () => closedByUser.current,
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

/** What the search will read and how long it will take, answered at once by the service before anything starts (plan=1). */
export interface DistantPlan {
  exposures: number;
  visits: number;
  hypotheses: number;
  estimate_s: number;
  dmin_used?: number;
  /** the pass the search will read (0-based): the pass on show, or the one with the visits it needs */
  pass?: number;
}

export function useDistantPlan(aim: DistantAim, enabled: boolean): DistantPlan | null {
  const [plan, setPlan] = useState<DistantPlan | null>(null);
  useEffect(() => {
    setPlan(null);
    if (!enabled) return;
    const ctl = new AbortController();
    const q = new URLSearchParams({
      ra: String(aim.ra),
      dec: String(aim.dec),
      fov: String(aim.fov >= 20 ? 18 : aim.fov),
      dmin: String(aim.dmin ?? DMIN),
      dmax: String(aim.dmax ?? DMAX),
      plan: '1',
    });
    if (aim.det) q.set('det', aim.det);
    if (aim.pass != null) q.set('pass', String(aim.pass));
    else if (aim.mjd != null) q.set('mjd', String(aim.mjd));
    fetch(`/api/v1/distant?${q}`, { signal: ctl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: (DistantPlan & { status?: string }) | null) => {
        if (d && d.status === 'plan') setPlan(d);
      })
      .catch(() => {});
    return () => ctl.abort();
  }, [aim.ra, aim.dec, aim.fov, aim.pass, aim.mjd, enabled]);
  return plan;
}

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
  }, [
    s.state === 'ok' ? s.r : null,
    s.state === 'ok' ? s.bin : null,
    s.state === 'ok' ? s.di : 0,
    s.state === 'ok' ? s.mu[0] : 0,
    s.state === 'ok' ? s.mu[1] : 0,
    s.state === 'ok' && s.neg,
  ]);
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
  plan,
  currentPass,
  passLabel,
  onPick,
  auto,
}: {
  d: Distant;
  /** from useDistantPlan: shown before the search starts */
  plan?: DistantPlan | null;
  /** the pass on show in the picture (0-based) and its label, for the button */
  currentPass: number;
  passLabel?: string;
  /** a peak was chosen: the owner opens its card (see distantFind) */
  onPick?: (c: DistantCandidate, i: number, r: DistantResult) => void;
  /** the search runs on its own: nothing to set or to start here, only its result (no pass buttons, no sliders) */
  auto?: boolean;
}) {
  const { ds } = d;
  const reading = useMemo(() => (ds.state === 'ok' ? readText(ds, d.peak) : ''), [ds, d.peak]);
  if (d.available !== true) return null;
  if (ds.state === 'idle')
    return (
      <div class="dsbox">
        <div
          class="srrow"
          title="Too faint for any single frame, a body this far out shows only when the visits of a pass are added along its motion. Drag a distance slider and watch a point come into focus. Reads every exposure of this field (D1–D3, all passes): a few minutes the first time."
        >
          <span class="srt">Find Planet X</span>
          <button class="btn sm" onClick={() => void d.start(null)}>
            Search
          </button>
        </div>
        <div class="mono-s">
          {plan?.pass != null && plan.pass !== currentPass
            ? `searches pass ${plan.pass + 1} (the pass on show, ${currentPass + 1}, is not the one with the visits it needs)`
            : `searches the pass on show: pass ${currentPass + 1}${passLabel ? ` · ${passLabel}` : ''}`}{' '}
          · distance {d.range.dmin}–{d.range.dmax} au
          {d.range.det !== '123' ? ` · D${d.range.det.split('').join(', D')}` : ''} ·{' '}
          {plan
            ? `${plan.exposures} exposures in ${plan.visits} visits · ${plan.hypotheses.toLocaleString('en-US')} orbits · ${about(plan.estimate_s)} the first time`
            : 'a few minutes the first time'}
        </div>
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
          {!auto && (
          <div class="chips">
            {ds.r.passes.map((p) => (
              <button
                key={p.index}
                class={`chip${p.index === ds.r.pass ? ' on' : ''}`}
                title={`${p.n} exposures`}
                onClick={() => void d.start(p.index)}
              >
                pass {p.index + 1} · {utDate(p.mjd0)}
              </button>
            ))}
          </div>
          )}
          <div class="mono-s">{reading}</div>
          {!auto && (
            <>
              <Slider label="Distance" min={0} max={ds.r.slider.distances.length - 1} value={ds.di} onInput={d.setDi} />
              <Slider label="Own motion, east–west" min={-100} max={100} value={ds.mu[0]} onInput={(v) => d.setMu([v, ds.mu[1]])} />
              <Slider label="Own motion, north–south" min={-100} max={100} value={ds.mu[1]} onInput={(v) => d.setMu([ds.mu[0], v])} />
              <label class="ds-chk">
                <input type="checkbox" checked={ds.neg} onChange={(e) => d.setNeg((e.target as HTMLInputElement).checked)} /> Show the noise:
                the same sum of the negated differences
              </label>
            </>
          )}
          <DistantLines r={ds.r} />
          {!auto && ds.r.candidates.map((c, i) => !c.ok ? null : (
            <button
              key={i}
              class={`btn sm leadbtn${ds.sel === i ? ' on' : ''}`}
              onClick={() => {
                d.select(i);
                onPick?.(c, i, ds.r);
              }}
            >
              <b>Peak {i + 1}</b> · S/N {c.sn} ·{' '}
              {ds.r.n_hypotheses < 10 ? `${c.d} au (one orbit tried: the distance is not measured)` : c.edge ? `${c.d_lo} au or farther (at the edge of the range: not bounded)` : `${c.d} au (${c.d_lo}–${c.d_hi})`} · photometry{' '}
              {c.phot_sn != null ? `${c.phot_sn}σ` : 'n/a'}
              {c.ok ? ' · confirmed' : ''}
              {knownName(c.known) ? ` · ${knownName(c.known)}` : ''}
            </button>
          ))}
        </>
      )}
    </div>
  );
}

function Slider({
  label,
  min,
  max,
  value,
  onInput,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  onInput: (v: number) => void;
}) {
  return (
    <>
      <label class="ds-lab">{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={value}
        aria-label={label}
        onInput={(e) => onInput(Number((e.currentTarget as HTMLInputElement).value))}
      />
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
  return (
    all
      .slice(0, 4)
      .map(([k, n]) => (n > 1 ? `${k} ×${n}` : k))
      .join(' · ') + (all.length > 4 ? ` · and ${all.length - 4} more kinds` : '')
  );
}

/** The status lines of a result, in the words of the search: what it did, what the noise reaches, how many peaks, how far a blind search reaches. */
export function DistantLines({ r }: { r: DistantResult }) {
  const nl = r.null?.[0];
  const lo = r.dmin_used ?? r.params?.dmin ?? r.dmin ?? DMIN;
  const hi = r.params?.dmax ?? r.dmax ?? DMAX;
  const L = (t: string, tip?: string) => (
    <div class="mono-s stl" title={tip}>
      {t}
    </div>
  );
  return (
    <>
      {L(
        `${r.n_hypotheses.toLocaleString('en-US')} ${r.n_hypotheses === 1 ? 'orbit' : 'orbits'} tried · ${lo}–${hi} au · ${r.scan_secs} s`,
        'Hypotheses of distance and own motion scanned for this pass.',
      )}
      {r.n_hypotheses < 10 &&
        L(
          `one pass spans ${r.span_days != null ? `${r.span_days.toFixed(1)} days` : 'a few days'}: at ${lo}–${hi} au a body moves less than a pixel in that time, so this pass alone cannot tell the distances apart — the deep sum of the passes can`,
        )}
      {L(
        `noise reaches S/N ${nl != null ? nl.toFixed(1) : '?'} · bar ${r.threshold.toFixed(1)}`,
        'The same sum of the negated differences gives the noise ceiling; a peak counts above the bar.',
      )}
      {L(
        r.candidates.filter((c) => c.ok).length
          ? `${plural(r.candidates.filter((c) => c.ok).length, 'lead')} (peaks the photometry confirms${r.n_hypotheses < 10 ? `, of the ${r.n_hypotheses} ${r.n_hypotheses === 1 ? 'orbit' : 'orbits'} this pass can test` : ''}) · a lead, not a discovery`
          : r.candidates.length
            ? `${plural(r.candidates.length, 'peak')} above the bar, none confirmed by the photometry · not a lead`
            : 'nothing above the bar · nothing marked',
      )}
      {r.candidates.some((c) => c.residual) &&
        L(
          `${plural(r.candidates.filter((c) => c.residual).length, 'peak')} above the bar left out as residuals (a bright star's halo, or a signal that grows to the edge of the searched distances): not leads`,
          r.candidates
            .filter((c) => c.residual)
            .map((c) => `${c.d} au · S/N ${c.sn}: ${c.residual}`)
            .join('\n'),
        )}
      {L(
        `${r.control.measured} control tracks · ${r.control.reach_sn5} reach S/N 5`,
        'Photometry on the exposures along the track and on control tracks aside.',
      )}
      {L(
        'blind limit about V 20–21 · reflected light',
        'One blind search of this kind reaches about V 20–21; a Planet Nine of the predicted brightness is fainter.',
      )}
      {L(
        'Planet Nine: margin 1.4–1.7× only in D4 (3 µm)',
        'The one place SPHEREx adds to the existing Planet Nine searches is 3 µm (D4); the margin over the WISE search is small.',
      )}
      {r.notes && r.notes.length > 0 && <div class="mono-s">{noteText(r.notes)}</div>}
    </>
  );
}

/** The rows of the card of a peak (the owner shows them in its find card). */
export function peakRows(c: DistantCandidate, r: DistantResult): [string, string, string][] {
  return [
    [
      'Signal',
      `S/N ${c.sn} in the sum · ${c.phot_sn != null ? `${c.phot_sn} σ` : 'n/a'} in photometry`,
      'the sum over the visits along one orbit, and PSF photometry along it',
    ],
    [
      'Bar and noise',
      `bar ${r.threshold.toFixed(1)} · noise reaches ${r.null?.[0]?.toFixed(1) ?? '?'}`,
      'the highest peak of the negated sum',
    ],
    ['Distance', `${c.d} au (${c.d_lo}–${c.d_hi})`, 'of the best orbit, and the range the knob keeps within 10 % of the peak'],
    ['Own motion', `${c.mu[0]}, ${c.mu[1]} ″/day`, 'east, north'],
    ['Control tracks', c.control_sn?.length ? `${c.control_sn.join(', ')} σ` : 'n/a', 'tracks beside the peak'],
    ['Reach', 'a blind search reaches about V 20–21; Planet Nine margin 1.4–1.7× only in D4', 'in reflected light'],
  ];
}

/** The picture of a lead: the visits of its pass shifted along ITS orbit (its distance and own motion) and added, cut round the lead's place; the lead is the point that
 *  comes into focus. Null when the grid does not hold the place. */
export function leadStamp(
  s: Extract<DistantState, { state: 'ok' }>,
  c: DistantCandidate,
  ra0: number,
  dec0: number,
  half = 18,
): { size: number; rgba: Uint8ClampedArray; peak: number } | null {
  const sl = s.r.slider;
  const n = sl.n;
  const nn = n * n;
  // the distance of the slider nearest the lead's, and its own motion in grid pixels per day
  let di = 0;
  sl.distances.forEach((d, k) => {
    if (Math.abs(d - c.d) < Math.abs(sl.distances[di]! - c.d)) di = k;
  });
  const sh = sl.offsets_px[di]!;
  const mu = [c.mu[0] / sl.step, c.mu[1] / sl.step];
  const out = new Float32Array(nn);
  const den = new Float32Array(nn);
  for (let i = 0; i < sl.t.length; i++) {
    const om = 1 / sl.sg[i]!;
    const sx = Math.round(sh[i]![0] + mu[0]! * sl.dt[i]!);
    const sy = Math.round(sh[i]![1] + mu[1]! * sl.dt[i]!);
    if (Math.abs(sx) >= n || Math.abs(sy) >= n) continue;
    const base = i * nn;
    for (let y = Math.max(0, -sy); y < n - Math.max(0, sy); y++) {
      let o = y * n + Math.max(0, -sx);
      let q = base + (y + sy) * n + Math.max(0, -sx) + sx;
      for (let x = Math.max(0, -sx); x < n - Math.max(0, sx); x++, o++, q++) {
        const v = s.bin[q]!;
        if (v) {
          out[o]! += om * ((v - 128) / 25);
          den[o]! += om * om;
        }
      }
    }
  }
  let wmax = 0;
  for (let p = 0; p < nn; p++) if (den[p]! > wmax) wmax = den[p]!;
  const sn = new Float32Array(nn);
  const samp: number[] = [];
  for (let p = 0; p < nn; p++)
    if (den[p]! >= 0.6 * wmax) {
      sn[p] = out[p]! / Math.sqrt(den[p]!);
      if (p % 9 === 0) samp.push(sn[p]!);
    }
  samp.sort((a, b) => a - b);
  const md = samp[samp.length >> 1] ?? 0;
  const ad = samp.map((x) => Math.abs(x - md)).sort((a, b) => a - b);
  const spread = Math.max(1.4826 * (ad[ad.length >> 1] ?? 1), 0.5);
  // the lead's place in the grid (x grows as RA falls, y with Dec), and the brightest point within 4 pixels of it
  const px0 = Math.round(n / 2 - ((((c.ra - ra0 + 540) % 360) - 180) * Math.cos((dec0 * Math.PI) / 180) * 3600) / sl.step);
  const py0 = Math.round(n / 2 + ((c.dec - dec0) * 3600) / sl.step);
  let bx = px0;
  let by = py0;
  let best = -1e9;
  for (let y = py0 - 4; y <= py0 + 4; y++)
    for (let x = px0 - 4; x <= px0 + 4; x++) {
      if (x < 0 || y < 0 || x >= n || y >= n) continue;
      const v = sn[y * n + x]!;
      if (v > best) {
        best = v;
        bx = x;
        by = y;
      }
    }
  if (bx < half || by < half || bx >= n - half || by >= n - half) return null;
  const size = 2 * half + 1;
  const rgba = new Uint8ClampedArray(size * size * 4);
  for (let yy = 0; yy < size; yy++)
    for (let xx = 0; xx < size; xx++) {
      const p = (by + half - yy) * n + (bx - half + xx);
      const o4 = (yy * size + xx) * 4;
      if (den[p]! < 0.6 * wmax) {
        rgba.set([6, 12, 16, 255], o4);
        continue;
      }
      const t = Math.max(0, Math.min(1, ((sn[p]! - md) / spread) / 8));
      rgba.set([14 + 241 * t, 18 + 170 * t * t, 24 + 40 * t * t * t, 255], o4);
    }
  return { size, rgba, peak: (best - md) / spread };
}

/** The lead's stamp and numbers for the passport: the summed picture along its orbit, distance, S/N, verdict and the known body. */
export function LeadPassport({ ds, c, ra0, dec0 }: { ds: Extract<DistantState, { state: 'ok' }>; c: DistantCandidate; ra0: number; dec0: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const st = useMemo(() => leadStamp(ds, c, ra0, dec0), [ds, c, ra0, dec0]);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !st) return;
    const tmp = document.createElement('canvas');
    tmp.width = tmp.height = st.size;
    tmp.getContext('2d')!.putImageData(new ImageData(st.rgba, st.size, st.size), 0, 0);
    const g = cv.getContext('2d')!;
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';
    g.clearRect(0, 0, cv.width, cv.height);
    g.drawImage(tmp, 0, 0, cv.width, cv.height);
    g.strokeStyle = 'rgba(95,227,214,.9)';
    g.lineWidth = 1;
    const m = cv.width / 2;
    for (const [x0, y0, x1, y1] of [[m, 4, m, m - 10], [m, m + 10, m, cv.height - 4], [4, m, m - 10, m], [m + 10, m, cv.width - 4, m]] as const) {
      g.beginPath();
      g.moveTo(x0, y0);
      g.lineTo(x1, y1);
      g.stroke();
    }
  }, [st]);
  const kn = knownName(c.known);
  const verdict = c.ok
    ? 'a lead: the photometry along its orbit confirms it'
    : c.residual
      ? `not a lead: ${c.residual}`
      : 'above the bar, but the photometry along its orbit does not confirm it';
  return (
    <div class="lead-pass" data-testid="lead-passport">
      <canvas ref={ref} width={132} height={132} title="The visits of the pass shifted along this lead's orbit and added: the point in the middle is the lead" />
      <div class="lp-txt">
        <b>Find Planet X lead · deep sum along its orbit</b>
        <div>
          distance {c.d.toFixed(0)} au ({c.d_lo.toFixed(0)}–{c.d_hi.toFixed(0)}){c.edge ? ', not bounded' : ''}
        </div>
        <div>
          S/N {c.sn.toFixed(1)}σ{c.phot_sn != null ? ` · photometry ${c.phot_sn.toFixed(1)}σ` : ''}
        </div>
        <div>{verdict}</div>
        <div>{kn ? `known body: ${kn}` : c.known?.state === 'ok' ? `known body: ${c.known.name}` : 'no known body there'}</div>
        {!st && <div class="faint">the lead is too near the edge of the grid for a stamp</div>}
      </div>
    </div>
  );
}
