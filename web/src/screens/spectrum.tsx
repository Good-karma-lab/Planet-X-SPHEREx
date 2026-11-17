import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { atEpoch, formatDec, formatRA } from '../lib/sky';
import { binNote, binSpectrum, type Bin } from './spectrum/bins';
import { CatalogueCheck, REF_LAM, useCatalogue, useReferencePhotometry } from './spectrum/catalogue';
import { quickPoints, useQuicklook, type QuickPoint } from './spectrum/quicklook';
import { StampPanels, useStamps } from './spectrum/stamps';
import { guessObject } from './spectrum/classify';
import type { Source, SpecPoint } from './spectrum/types';
import type { Held } from './lab/known';
import './spectrum/extra.css';

export type { Source, SpecPoint };

interface SpecView {
  key: string;
  state: 'running' | 'done' | 'failed';
  error: string;
  total: number;
  measured: number;
  usable: number;
  saturated: number;
  no_spectrum: boolean;
  points: SpecPoint[];
}

const DET_HUE = [270, 220, 175, 95, 40, 5];

/** Follows one spectrum as it is measured: the points arrive in pieces (`since` = points already held). */

/** Gaia's values as people read them: G and BP−RP to 0.01 mag, the parallax to 0.01 mas (with its error when known), RUWE to 0.01. */
function gaiaLine(ga: { source_id?: string; g?: number | null; bp_rp?: number | null; parallax?: number | null; ruwe?: number | null; parallax_error?: number | null }): string {
  const parts = [String(ga.source_id ?? '')];
  if (ga.g != null) parts.push(`G ${ga.g.toFixed(2)}`);
  if (ga.bp_rp != null) parts.push(`BP−RP ${ga.bp_rp.toFixed(2)}`);
  if (ga.parallax != null) parts.push(`parallax ${ga.parallax.toFixed(2)}${ga.parallax_error != null ? ` ± ${ga.parallax_error.toFixed(2)}` : ''} mas`);
  if (ga.ruwe != null) parts.push(`RUWE ${ga.ruwe.toFixed(2)}`);
  return parts.join(' · ');
}

export function useSpectrum(src: Source | null): {
  view: SpecView | null;
  points: SpecPoint[];
  error: string;
} {
  const [view, setView] = useState<SpecView | null>(null);
  const [points, setPoints] = useState<SpecPoint[]>([]);
  const [error, setError] = useState('');
  // the object last followed, at the date of the passes: the same object picked again (at its J2000 or its present place) keeps what is on show while its
  // spectrum is asked for again, so the state does not fall back to "Finding the exposures…" and a count that was 54 does not restart at 0
  const last = useRef<{ ra: number; dec: number; key: string } | null>(null);
  useEffect(() => {
    if (!src) {
      last.current = null;
      setView(null);
      setPoints([]);
      setError('');
      return;
    }
    const at = src.pmra || src.pmdec ? atEpoch(src.ra, src.dec, src.pmra ?? 0, src.pmdec ?? 0, 2025.9) : ([src.ra, src.dec] as [number, number]);
    const prev = last.current;
    const same = !!prev && Math.hypot((prev.ra - at[0]) * Math.cos((at[1] * Math.PI) / 180), prev.dec - at[1]) * 3600 < 4;
    last.current = { ra: at[0], dec: at[1], key: '' };
    if (!same) {
      setView(null);
      setPoints([]);
    }
    setError('');
    let stop = false;
    let held: SpecPoint[] = [];
    let first = true;
    const q = new URLSearchParams({
      ra: src.ra.toFixed(6),
      dec: src.dec.toFixed(6),
    });
    if (src.pmra || src.pmdec) {
      q.set('pmra', String(src.pmra ?? 0));
      q.set('pmdec', String(src.pmdec ?? 0));
    }
    const poll = async () => {
      while (!stop) {
        try {
          const r = await fetch(`/api/v1/spectrum?${q}&since=${held.length}`, {
            cache: 'no-store',
          });
          if (r.status === 429) {
            setError('Too many new measurements from this address; trying again shortly');
            await new Promise((res) => setTimeout(res, 5000));
            continue;
          }
          if (!r.ok) throw new Error(`service answered ${r.status}`);
          const v = (await r.json()) as SpecView;
          if (stop) return;
          held = [...held, ...v.points];
          // a measurement that starts again for the same object never shows a smaller count than the one on show
          setView((old) => (same && first && old && old.state === 'running' && v.state === 'running' && v.measured < old.measured ? old : v));
          first = false;
          setPoints((old) => (held.length >= old.length || !same ? held : old));
          setError(v.state === 'failed' ? v.error : '');
          if (v.state !== 'running') {
            // the finished spectrum once more in full: points that deviate from their neighbours are flagged only at the end
            const all = (await fetch(`/api/v1/spectrum?${q}&since=0`, {
              cache: 'no-store',
            }).then((x) => x.json())) as SpecView;
            if (!stop) setPoints(all.points);
            return;
          }
        } catch (e) {
          setError(String(e));
        }
        await new Promise((res) => setTimeout(res, 350));
      }
    };
    void poll();
    return () => {
      stop = true;
    };
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec]);
  return { view, points, error };
}

interface RefPoint {
  band: string;
  lam: number;
  mjy: number;
}

function SpectrumChart({
  points,
  bins,
  cur,
  quick,
  refs,
}: {
  points: SpecPoint[];
  bins: Bin[];
  cur: SpecPoint | null;
  /** the all-sky cube's own values in mJy per cube pixel, only while no measured point exists */
  quick: QuickPoint[] | null;
  /** catalogue photometry (mJy) drawn as open diamonds: the reference of a saturated star */
  refs: RefPoint[];
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = cv.clientWidth;
    const H = cv.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    const c = cv.getContext('2d')!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const ok = points.filter((p) => p.ok && p.flux != null);
    const L = 42;
    const R = 8;
    const T = 20;
    const B = 26;
    const x = (lam: number) => L + ((lam - 0.7) / (5.05 - 0.7)) * (W - L - R);
    let lo = 0;
    let hi = 1;
    const vals = [...ok.map((p) => p.flux!), ...(quick ?? []).map((q) => q.mjy), ...refs.map((r) => r.mjy)].sort((a, b) => a - b);
    if (vals.length) {
      // measured points and quick look: the 1st to 99th percentile; catalogue references always stay inside the axis
      const ref = refs.map((r) => r.mjy);
      lo = Math.min(0, vals[Math.floor(0.01 * (vals.length - 1))]!);
      hi = Math.max(vals[Math.floor(0.99 * (vals.length - 1))]!, ...ref) * 1.15;
      if (hi <= lo) hi = lo + 1;
    }
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    c.strokeStyle = 'rgba(140,225,240,0.18)';
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10.5px ui-monospace, Menlo, monospace';
    c.lineWidth = 1;
    for (let lam = 1; lam <= 5; lam++) {
      c.beginPath();
      c.moveTo(x(lam), T);
      c.lineTo(x(lam), H - B);
      c.stroke();
      c.fillText(`${lam}`, x(lam) - 3, H - B + 13);
    }
    c.fillText('µm', W - R - 14, H - 4);
    const step = niceStep((hi - lo) / 4);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      c.beginPath();
      c.moveTo(L, y(v));
      c.lineTo(W - R, y(v));
      c.stroke();
      c.fillText(fmt(v), 2, y(v) + 3);
    }
    c.fillText('mJy', 2, 12);
    if (lo < 0) {
      c.strokeStyle = 'rgba(214,231,237,0.35)';
      c.beginPath();
      c.moveTo(L, y(0));
      c.lineTo(W - R, y(0));
      c.stroke();
    }
    // the quick look of the all-sky cube, in the chart's own unit (mJy in one 12.9″ cube pixel), only while nothing is measured yet
    if (quick) {
      c.save();
      c.strokeStyle = 'rgba(200,215,222,0.7)';
      c.lineWidth = 1.4;
      c.beginPath();
      quick.forEach((q, i) => {
        if (i === 0) c.moveTo(x(q.lam), y(q.mjy));
        else c.lineTo(x(q.lam), y(q.mjy));
      });
      c.stroke();
      c.fillStyle = 'rgba(160,185,194,0.9)';
      c.font = '500 10px ui-monospace, Menlo, monospace';
      c.textAlign = 'right';
      c.fillText('quick look: all-sky map, one 12.9″ pixel', W - R - 4, T + 10);
      c.restore();
    }
    // catalogue photometry of a saturated star: open diamonds with the band's name
    if (refs.length) {
      c.save();
      c.strokeStyle = '#f3d27a';
      c.fillStyle = '#f3d27a';
      c.lineWidth = 1.6;
      c.font = '600 10px ui-monospace, Menlo, monospace';
      for (const r of refs) {
        const px = x(r.lam);
        const py = y(r.mjy);
        c.beginPath();
        c.moveTo(px, py - 5);
        c.lineTo(px + 5, py);
        c.lineTo(px, py + 5);
        c.lineTo(px - 5, py);
        c.closePath();
        c.stroke();
        c.fillText(r.band, px - 6, py - 9);
      }
      c.textAlign = 'right';
      c.fillText('◇ catalogue (2MASS, WISE)', W - R - 4, T + 10);
      c.restore();
    }
    for (const p of ok) {
      const col = `hsl(${DET_HUE[p.det - 1]},80%,62%)`;
      c.strokeStyle = col;
      c.globalAlpha = 0.45;
      c.beginPath();
      c.moveTo(x(p.lam), y(p.flux! - (p.err ?? 0)));
      c.lineTo(x(p.lam), y(p.flux! + (p.err ?? 0)));
      c.stroke();
      c.globalAlpha = 1;
      c.fillStyle = col;
      c.beginPath();
      c.arc(x(p.lam), y(p.flux!), 2.2, 0, 7);
      c.fill();
    }
    // the binned spectrum over the points: filled = at least 3σ above zero, an arrow down = 3σ upper limit
    for (const b of bins) {
      const col = `hsl(${DET_HUE[b.det - 1]},85%,72%)`;
      const bx = x(b.lam);
      c.strokeStyle = '#e8f6fa';
      c.fillStyle = '#071218';
      c.lineWidth = 1.5;
      if (b.detected) {
        c.beginPath();
        c.moveTo(bx, y(b.flux - b.err));
        c.lineTo(bx, y(b.flux + b.err));
        c.stroke();
        c.beginPath();
        c.arc(bx, y(b.flux), 4.2, 0, 7);
        c.fillStyle = col;
        c.fill();
        c.stroke();
      } else {
        const top = y(3 * b.err);
        c.beginPath();
        c.moveTo(bx, top);
        c.lineTo(bx, top + 12);
        c.moveTo(bx - 3.5, top + 8);
        c.lineTo(bx, top + 12.5);
        c.lineTo(bx + 3.5, top + 8);
        c.stroke();
      }
    }
    // the exposure being looked at: a ring on its point, or a tick at its wavelength when it gave no point
    if (cur && cur.lam > 0) {
      c.strokeStyle = '#ffffff';
      c.lineWidth = 1.4;
      if (cur.ok && cur.flux != null) {
        c.beginPath();
        c.arc(x(cur.lam), y(cur.flux), 6, 0, 7);
        c.stroke();
      } else {
        c.setLineDash([3, 3]);
        c.beginPath();
        c.moveTo(x(cur.lam), T);
        c.lineTo(x(cur.lam), H - B);
        c.stroke();
        c.setLineDash([]);
        // the dotted line says what it is: the exposure on show, which gave no point
        c.fillStyle = '#ffffff';
        c.font = '500 10px ui-monospace, Menlo, monospace';
        c.textAlign = x(cur.lam) > W / 2 ? 'right' : 'left';
        c.fillText('this exposure: no point', x(cur.lam) + (x(cur.lam) > W / 2 ? -4 : 4), H - B - 4);
        c.textAlign = 'left';
      }
    }
  }, [points, bins, cur, quick, refs]);
  return <canvas ref={ref} class="spec-chart" aria-label="Spectrum: flux against wavelength" />;
}

function niceStep(x: number): number {
  const e = 10 ** Math.floor(Math.log10(Math.max(x, 1e-9)));
  const m = x / e;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * e;
}
function fmt(v: number): string {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(0) : a >= 1 ? v.toFixed(1) : v.toFixed(2);
}

function csv(src: Source, points: SpecPoint[]): string {
  const head = `# SPHEREx Level-2 PSF photometry at RA ${src.ra.toFixed(6)} Dec ${src.dec.toFixed(6)}${src.pmra || src.pmdec ? ` (proper motion ${src.pmra} ${src.pmdec} mas/yr)` : ''}\n# i,mjd,detector,wavelength_um,flux_mJy,error_mJy,used,reason\n`;
  return head + points.map((p) => [p.i, p.mjd, p.det, p.lam, p.flux ?? '', p.err ?? '', p.ok ? 1 : 0, p.why].join(',')).join('\n') + '\n';
}

const PLAY_MS = 90;

function dateOf(mjd: number): string {
  return new Date((mjd - 40587) * 86400000).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

/** The slider over the exposures of a spectrum, in the order they were taken: one at a time, or played through. */
function ExposurePlayer({
  rows,
  k,
  onK,
  playing,
  onPlay,
}: {
  rows: SpecPoint[];
  k: number;
  onK: (k: number) => void;
  playing: boolean;
  onPlay: (on: boolean) => void;
}) {
  const cur = rows[k];
  const line = !cur
    ? ''
    : cur.ok && cur.flux != null
      ? `exposure ${k + 1} of ${rows.length} · ${dateOf(cur.mjd)} · D${cur.det} · ${cur.lam.toFixed(2)} µm · ${cur.flux.toFixed(Math.abs(cur.flux) < 10 ? 2 : 1)} mJy`
      : `exposure ${k + 1} of ${rows.length} · ${dateOf(cur.mjd)} · D${cur.det} · no point: ${cur.why || 'no usable measurement'}`;
  return (
    <div class="spec-play">
      <button
        class="pp"
        aria-label={playing ? 'Pause' : 'Play through the exposures'}
        onClick={() => onPlay(!playing)}
      >
        {playing ? (
          <svg viewBox="0 0 16 16"><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
        ) : (
          <svg viewBox="0 0 16 16"><path d="M4 2.2v11.6a.8.8 0 0 0 1.2.7l9-5.8a.8.8 0 0 0 0-1.4l-9-5.8A.8.8 0 0 0 4 2.2z" /></svg>
        )}
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(0, rows.length - 1)}
        step={1}
        value={k}
        aria-label="Exposure"
        onInput={(e) => {
          onPlay(false);
          onK(Number((e.currentTarget as HTMLInputElement).value));
        }}
      />
      <div class="spec-line">{line}</div>
    </div>
  );
}

export function SpectrumTab({ src }: { src: Source | null }) {
  // Clear drops the spectrum until another source is picked (a new pick is a new object, even at the same place)
  const [cleared, setCleared] = useState<Source | null>(null);
  const shown = src && src !== cleared ? src : null;
  const { view, points, error } = useSpectrum(shown);
  const { stamps, finished: stampsDone } = useStamps(shown, view?.state === 'running' || !view);
  const quick = useQuicklook(shown);
  const [at, setAt] = useState<number | null>(null); // null: the newest exposure
  const [playing, setPlaying] = useState(false);
  const rows = useMemo(() => [...points].sort((a, b) => a.i - b.i), [points]);
  const bins = useMemo(() => binSpectrum(points), [points]);
  const ident = useIdentify(shown);
  // a target that saturates the detector has no SPHEREx spectrum: say so once, show none of the per-exposure lines
  const saturated = !!view && (view.no_spectrum || (view.measured >= 20 && view.saturated > 0.5 * view.measured));
  // the all-sky quick look is only a placeholder: gone as soon as a measured point exists, never drawn from a mostly empty column
  const qpts = useMemo(() => quickPoints(quick), [quick]);
  const showQuick = qpts && !points.some((p) => p.ok);
  const done = view?.state === 'done';
  const cat = useCatalogue(shown, done && points.length > 0 && !saturated);
  const reference = useReferencePhotometry(shown, done && saturated);
  const refs = useMemo<RefPoint[]>(
    () =>
      !saturated || reference.state !== 'done'
        ? []
        : reference.rows.filter((r) => r.cat_mjy != null && REF_LAM[r.band] != null).map((r) => ({ band: r.band, lam: REF_LAM[r.band]!, mjy: r.cat_mjy! })),
    [saturated, reference],
  );
  const k = at == null ? Math.max(0, rows.length - 1) : Math.min(at, Math.max(0, rows.length - 1));
  useEffect(() => {
    setAt(null);
    setPlaying(false);
  }, [shown]);
  useEffect(() => {
    if (!playing || rows.length < 2) return;
    // a play starts from the first exposure unless the slider is in the middle
    const t = setInterval(() => {
      setAt((v) => {
        const nx = (v ?? 0) + 1;
        if (nx >= rows.length - 1) {
          setPlaying(false);
          return rows.length - 1;
        }
        return nx;
      });
    }, PLAY_MS);
    return () => clearInterval(t);
  }, [playing, rows.length]);
  if (!shown) return <div class="mono-s">Click a source in the picture</div>;
  const why = new Map<string, number>();
  for (const p of points) if (!p.ok) why.set(p.why, (why.get(p.why) ?? 0) + 1);
  const perDet = [1, 2, 3, 4, 5, 6].map((d) => points.filter((p) => p.ok && p.det === d).length);
  const usable = points.filter((p) => p.ok).length;
  const cur = rows[k] ?? null;
  const sb = ident && ident !== 'asking' ? ident.simbad : undefined;
  const guess = guessObject({
    bins,
    usable,
    saturated,
    running: !view || view.state === 'running',
    name: shown.name ?? (sb?.state === 'ok' ? sb.name : null),
    type: sb?.state === 'ok' ? sb.type : null,
    sp: sb?.state === 'ok' ? sb.sp : null,
  });
  return (
    <div class="spec">
      <div class="spec-head">
        <b>{shown.name ?? 'Source'}</b>
        <span class="mono-s">
          {formatRA(shown.ra)} {formatDec(shown.dec)}
        </span>
        <button class="lnk spec-clear" onClick={() => setCleared(shown)}>
          Clear
        </button>
      </div>
      <div class="spec-state">
        {!view || (view.state === 'running' && view.total === 0)
          ? 'Finding the exposures that cover this place…'
          : view.state === 'running'
            ? `Measuring: ${view.measured} of ${view.total} exposures that cover this place · ${usable} used`
            : view.state === 'done'
              ? // a saturated star: the amber line below says it once
                saturated
                ? ''
                : guess.head
              : `Failed: ${view.error}`}
      </div>
      {view?.state === 'done' && guess.features && <div class="spec-feat">{guess.features}</div>}
      {(!view || view.state === 'running') && (
        <div class={`spec-prog${!view || view.total === 0 ? ' indet' : ''}`} aria-label="Measuring the spectrum">
          <i style={view && view.total > 0 ? `width:${Math.round((100 * view.measured) / view.total)}%` : ''} />
        </div>
      )}
      {saturated && (
        <div class="partial">Saturated in most exposures: no SPHEREx spectrum (the core of this star overflows the detector)</div>
      )}
      <SpectrumChart points={points} bins={bins} cur={saturated ? null : cur} quick={showQuick ? qpts : null} refs={refs} />
      {(bins.length > 0 || view?.state === 'done') && (
        <div class="spec-bin-note">
          {view?.state === 'done' ? `${usable} of ${view.total} exposures usable` : ''}
          {view?.state === 'done' && bins.length > 0 ? ' · ' : ''}
          {bins.length > 0 ? binNote(bins) : ''}
        </div>
      )}
      <div class="spec-dets">
        {perDet.map((n, i) => (
          <span key={i}>
            <span class="sw" style={{ background: `hsl(${DET_HUE[i]},80%,62%)` }} />D{i + 1} {n}
          </span>
        ))}
      </div>
      {rows.length > 1 && !saturated && (
        <ExposurePlayer
          rows={rows}
          k={k}
          onK={(v) => setAt(v)}
          playing={playing}
          onPlay={(on) => {
            if (on && (at == null || at >= rows.length - 1)) setAt(0);
            setPlaying(on);
          }}
        />
      )}
      {cur && !saturated && (
        <StampPanels
          stamp={stamps.get(cur.i)}
          note={!cur.ok ? `no fit: ${cur.why || 'no usable measurement'}` : undefined}
        />
      )}
      {cur && !saturated && stampsDone && stamps.size === 0 && <div class="mono-s">The pixels were not kept for this spectrum (it was measured before they were stored).</div>}
      {why.size > 0 && !saturated && <div class="mono-s">Not used: {[...why.entries()].map(([k2, n]) => `${n} ${k2}`).join(' · ')}</div>}
      {error && (
        <div class="mono-s" style={{ color: 'var(--amber)' }}>
          {error}
        </div>
      )}
      <CatalogueCheck a={saturated ? reference : cat} ready={done && (points.length > 0 || saturated)} saturated={saturated} />
      {view?.state === 'done' && usable > 0 && (
        <a
          class="lnk"
          download={`spectrum-${formatRA(shown.ra, true)}${formatDec(shown.dec, true)}.csv`}
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(csv(shown, points))}`}
        >
          Download the points (CSV)
        </a>
      )}
    </div>
  );
}

interface Identified {
  simbad?: { state: string; name?: string; type?: string; sp?: string | null; sep?: number; error?: string };
  gaia?: {
    state: string;
    source_id?: string;
    g?: number | null;
    bp_rp?: number | null;
    parallax?: number | null;
    ruwe?: number | null;
    parallax_error?: number | null;
    error?: string;
  };
}

/** What the field's lookup already holds, in the passport's terms (the catalogues it did not cover are left out: they are asked). */
export function heldIdentified(h: Held | null | undefined): Identified {
  if (!h) return {};
  return {
    simbad: h.simbad === 'none' ? { state: 'none' } : h.simbad ? { state: 'ok', name: h.simbad.name, type: h.simbad.type ?? undefined, sp: null } : undefined,
    gaia: h.gaia
      ? { state: 'ok', source_id: h.gaia.source_id, g: h.gaia.g, parallax: h.gaia.parallax, bp_rp: h.gaia.bp_rp, ruwe: h.gaia.ruwe }
      : h.covered.includes('gaia')
        ? { state: 'none' }
        : undefined,
  };
}

/** What the passport asks /api/v1/context for a source: null when the field's lookup already answered both SIMBAD and Gaia (nothing to
 *  ask), else the catalogues to skip (those it did answer). */
export function identifySkip(held: Held | null | undefined): string[] | null {
  const covered = held?.covered ?? [];
  return covered.includes('gaia') && covered.includes('simbad') ? null : [...covered];
}

/** What SIMBAD and Gaia DR3 call a place, asked once per selection. `held`: what the field's lookup already answered for it (a ringed
 *  known star): taken as it is, and only the catalogues it did not cover are asked; nothing is asked when it covered both. */
export function useIdentify(src: Source | null, held?: Held | null): Identified | null | 'asking' {
  const [v, setV] = useState<Identified | null | 'asking'>(null);
  const covered = held?.covered ?? [];
  const heldKey = held ? `${held.name}|${covered.join(',')}` : '';
  useEffect(() => {
    setV(null);
    if (!src) return;
    const base = heldIdentified(held);
    const skip = identifySkip(held);
    if (skip === null) {
      setV(base);
      return;
    }
    setV(held ? base : 'asking');
    const ctl = new AbortController();
    // a place on the picture is where the thing is in 2025.9: ask the catalogues at that epoch (they move their stars); a catalogue object
    // that carries a proper motion has its J2000 place, so epoch 2000
    const moving = !!(src.pmra || src.pmdec);
    const cq = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6), epoch: moving ? '2000' : '2025.9', radius: '6' });
    if (skip.length) cq.set('skip', skip.join(','));
    const old = new URLSearchParams({ ra: src.ra.toFixed(6), dec: src.dec.toFixed(6) });
    if (moving) {
      old.set('pmra', String(src.pmra ?? 0));
      old.set('pmdec', String(src.pmdec ?? 0));
    }
    const fromContext = (c: {
      simbad?: { state: string; name?: string; otype?: string; sptype?: string | null; sep?: number };
      gaia?: Identified['gaia'];
    }): Identified => ({
      simbad: base.simbad ?? (c.simbad && { state: c.simbad.state, name: c.simbad.name, type: c.simbad.otype, sp: c.simbad.sptype ?? null, sep: c.simbad.sep }),
      gaia: base.gaia ?? c.gaia,
    });
    // a catalogue not in yet comes back `pending`: ask again every 2 s, up to 20 s, showing each answer as it lands
    const pendingIn = (c: { simbad?: { state: string }; gaia?: { state: string } }) => c.simbad?.state === 'pending' || c.gaia?.state === 'pending';
    const ask = async (n: number): Promise<void> => {
      const r = await fetch(`/api/v1/context?${cq}`, { signal: ctl.signal });
      if (!r.ok) throw new Error(String(r.status));
      const c = await r.json();
      setV(fromContext(c));
      if (pendingIn(c) && n < 10) {
        await new Promise((res) => setTimeout(res, 2000));
        if (!ctl.signal.aborted) await ask(n + 1);
      }
    };
    ask(0)
      .catch((e) => {
        if (ctl.signal.aborted) return;
        // the service has no context route: the older lookup (J2000 place and proper motion)
        void e;
        fetch(`/api/v1/identify?${old}`, { signal: ctl.signal })
          .then((r) => (r.ok ? r.json() : null))
          .then((d: Identified | null) => setV(d ? { simbad: base.simbad ?? d.simbad, gaia: base.gaia ?? d.gaia } : held ? base : null))
          .catch(() => {});
      });
    return () => ctl.abort();
  }, [src?.ra, src?.dec, src?.pmra, src?.pmdec, heldKey]);
  return v;
}

/** What the object probably is, read from its spectrum: the same words as the Spectrum tab's header (one function behind both). Shown in the passport beside the portrait. */
export function ObjectGuess({ src, held }: { src: Source | null; held?: Held | null }) {
  const { view, points } = useSpectrum(src);
  const id = useIdentify(src, held);
  const bins = useMemo(() => binSpectrum(points), [points]);
  if (!src) return null;
  const usable = points.filter((p) => p.ok).length;
  const saturated = !!view && (view.no_spectrum || (view.measured >= 20 && view.saturated > 0.5 * view.measured));
  const sb = id && id !== 'asking' ? id.simbad : undefined;
  const g = guessObject({
    bins,
    usable,
    saturated,
    running: !view || view.state === 'running',
    name: src.name ?? (sb?.state === 'ok' ? sb.name : (held?.name ?? null)),
    type: sb?.state === 'ok' ? sb.type : null,
    sp: sb?.state === 'ok' ? sb.sp : null,
  });
  return (
    <div class="obj-guess" data-testid="object-guess">
      <div>{!view ? 'Reading its spectrum…' : view.state === 'failed' ? 'No spectrum to read a class from' : g.head}</div>
      {g.features && <div class="spec-feat">{g.features}</div>}
    </div>
  );
}

/** A source that some catalogue knows: Gaia, SIMBAD's other answer or the field's lookup (a ringed star, a deeper match). */
function knownElsewhere(ga: Identified['gaia'] | { state: string } | undefined, held: Held | null | undefined): boolean {
  return ga?.state === 'ok' || !!held;
}

/** The name of a source: its own, SIMBAD's, else the catalogue id the field's lookup or Gaia gives; "no catalogue name here" only for
 *  a source no catalogue knows. */
export function nameOf(src: Source, id: Identified | null | 'asking', held: Held | null | undefined): string {
  if (src.name) return src.name;
  const sb = id && id !== 'asking' ? id.simbad : undefined;
  const ga = id && id !== 'asking' ? id.gaia : undefined;
  if (sb?.state === 'ok' && sb.name) return sb.name;
  if (held?.name) return held.name;
  if (ga?.state === 'ok' && ga.source_id) return `Gaia DR3 ${ga.source_id}`;
  if (id === 'asking' || sb?.state === 'pending' || ga?.state === 'pending') return 'asking the catalogues…';
  return 'no catalogue name here';
}

export function ObjectTab({ src, onSpectrum, held, compact = false }: { src: Source | null; onSpectrum: () => void; held?: Held | null; compact?: boolean }) {
  // compact: the lab's passport, whose card head already gives the name and "Open spectrum"

  const id = useIdentify(src, held);
  if (!src) return <div class="mono-s">Click a source in the picture</div>;
  const pm = src.pmra || src.pmdec ? Math.hypot(src.pmra ?? 0, src.pmdec ?? 0) : 0;
  const sb = id && id !== 'asking' ? id.simbad : undefined;
  const ga0 = id && id !== 'asking' ? id.gaia : undefined;
  // a fast mover (a catalogue proper motion over 3″/yr) must be near: a Gaia source within 6″ with no parallax, or one far smaller than that motion allows (under 500 km/s on the sky),
  // is a neighbour that happens to stand there, not the object (WISE 0855, which Gaia does not have)
  const pmMas = src.pmKind !== 'measured' ? pm : 0;
  const neighbour = ga0?.state === 'ok' && pmMas >= 3000 && (ga0.parallax == null || ga0.parallax < (pmMas / 105.5) * 0.5);
  const ga = neighbour ? { state: 'none' } : ga0;
  const plx = ga?.state === 'ok' && ga.parallax != null && ga.parallax >= 3 && (ga.ruwe ?? 9) <= 1.4 ? ga.parallax : null;
  return (
    <div class="okv">
      <div class="orow" hidden={compact}>
        <span class="faint">Name</span>
        <span data-testid="obj-name">{nameOf(src, id, held)}</span>
      </div>
      <div class="orow">
        <span class="faint">Position</span>
        <span class="mono-s">
          {formatRA(src.ra)} {formatDec(src.dec)}
          {src.pmra || src.pmdec ? ' (J2000)' : ''}
        </span>
      </div>
      {pm > 0 && (
        <div class="orow">
          <span class="faint">Proper motion</span>
          <span>
            {pm >= 1000 ? `${(pm / 1000).toFixed(2)} ″/yr` : `${pm.toFixed(0)} mas/yr`}
            {src.pmKind === 'measured'
              ? `${src.pmErr ? ` ± ${src.pmErr >= 1000 ? (src.pmErr / 1000).toFixed(2) + ' ″/yr' : src.pmErr.toFixed(0) + ' mas/yr'}` : ''} · measured in the exposures`
              : ' · catalogue'}
          </span>
        </div>
      )}
      <div class="orow">
        <span class="faint">SIMBAD</span>
        <span>
          {id === 'asking'
            ? 'asking…'
            : sb?.state === 'ok'
              ? `${sb.name}${sb.type ? ` · ${sb.type}` : ''}${sb.sp ? ` · ${sb.sp}` : ''}${sb.sep != null ? ` (${sb.sep}″)` : ''}`
              : sb?.state === 'none'
                ? knownElsewhere(ga, held)
                  ? 'no entry'
                  : 'nothing within 6″'
                : sb?.state === 'pending'
                  ? 'asking…'
                  : 'not reachable'}
        </span>
      </div>
      <div class="orow">
        <span class="faint">Gaia DR3</span>
        <span>
          {id === 'asking'
            ? 'asking…'
            : ga?.state === 'ok'
              ? `${gaiaLine(ga)}${held?.gaia ? ' · from the field’s lookup' : ''}`
              : ga?.state === 'none' && held && !held.gaia && held.name
                ? 'no entry'
                : ga?.state === 'none'
                ? neighbour
                  ? `no match: the Gaia source within 6″ (G ${ga0?.g != null ? ga0.g.toFixed(1) : '?'}) has no parallax that fits a ${(pmMas / 1000).toFixed(1)} ″/yr mover`
                  : 'nothing within 6″'
                : ga?.state === 'pending'
                  ? 'asking…'
                  : 'not reachable'}
        </span>
      </div>
      {plx !== null && (
        <div class="orow">
          <span class="faint">Distance</span>
          <span>
            {(1000 / plx).toFixed(1)} pc · {((1000 / plx) * 3.2616).toFixed(1)} light-years (Gaia parallax)
          </span>
        </div>
      )}
      <div class="orow" hidden={compact}>
        <span class="faint">Spectrum</span>
        <span>
          <button class="lnk" onClick={onSpectrum}>
            Measure in every exposure
          </button>
        </span>
      </div>
    </div>
  );
}

export interface Anomaly {
  n: number;
  ra: number;
  dec: number;
  pmra: number;
  pmdec: number;
  moves: boolean;
  snr: number | null;
  score: number;
  points: number;
  drivers: { lo: number; hi: number; z: number; share: number }[];
  shape: (number | null)[];
  shape_err: (number | null)[];
  model: number[];
  resid_z: (number | null)[];
  identified?: {
    simbad?: { state: string; name?: string; type?: string; sp?: string | null; sep?: number };
    gaia?: { state: string; source_id?: string; g?: number | null; bp_rp?: number | null; parallax?: number | null; ruwe?: number | null; parallax_error?: number | null };
  };
}

export interface AnomalyField {
  sources: number;
  with_spectrum: number;
  median: number | null;
  scatter: number | null;
  threshold: number | null;
  reason: string;
  bins: number[];
  edges: number[];
  scores: number[];
  anomalies: Anomaly[];
  exposures: number;
  seconds: number;
}

/** The driving bins in words: "excess at 1.51–1.66 µm (+27σ, 31 % of the misfit)". */
export function driverWords(a: Anomaly): string[] {
  return a.drivers.map(
    (d) =>
      `${d.z > 0 ? 'excess' : 'deficit'} at ${d.lo.toFixed(2)}–${d.hi.toFixed(2)} µm (${d.z > 0 ? '+' : '−'}${Math.abs(d.z).toFixed(0)}σ, ${Math.round(d.share * 100)} % of the misfit)`,
  );
}

function PassportChart({ a, f }: { a: Anomaly; f: AnomalyField }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    const W = cv.clientWidth;
    const H = cv.clientHeight;
    cv.width = W * dpr;
    cv.height = H * dpr;
    const c = cv.getContext('2d')!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const L = 34;
    const R = 8;
    const T = 8;
    const B = 22;
    const lx = (lam: number) => L + ((Math.log(lam) - Math.log(0.75)) / (Math.log(5) - Math.log(0.75))) * (W - L - R);
    const vals = [...a.model, ...a.shape.filter((v): v is number => v != null)];
    const hi = Math.max(...vals) * 1.15;
    const lo = Math.min(0, ...vals);
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    c.strokeStyle = 'rgba(140,225,240,0.18)';
    c.fillStyle = 'rgba(160,185,194,0.9)';
    c.font = '500 10px ui-monospace, Menlo, monospace';
    for (const lam of [1, 2, 3, 4, 5]) {
      c.beginPath();
      c.moveTo(lx(lam), T);
      c.lineTo(lx(lam), H - B);
      c.stroke();
      c.fillText(String(lam), lx(lam) - 3, H - B + 12);
    }
    c.fillText('µm', W - R - 14, H - 3);
    // the misfit's drivers, shaded
    for (const d of a.drivers) {
      c.fillStyle = d.z > 0 ? 'rgba(255,90,168,0.16)' : 'rgba(53,216,238,0.16)';
      c.fillRect(lx(d.lo), T, lx(d.hi) - lx(d.lo), H - T - B);
    }
    // the ordinary star of the field, fitted to this source
    c.strokeStyle = 'rgba(214,231,237,0.85)';
    c.lineWidth = 1.5;
    c.beginPath();
    a.model.forEach((v, i) => (i ? c.lineTo(lx(f.bins[i]!), y(v)) : c.moveTo(lx(f.bins[i]!), y(v))));
    c.stroke();
    c.fillStyle = '#ffb454';
    c.strokeStyle = 'rgba(255,180,84,0.6)';
    a.shape.forEach((v, i) => {
      if (v == null) return;
      const e = a.shape_err[i] ?? 0;
      const x = lx(f.bins[i]!);
      c.beginPath();
      c.moveTo(x, y(v - e));
      c.lineTo(x, y(v + e));
      c.stroke();
      c.beginPath();
      c.arc(x, y(v), 2.6, 0, 7);
      c.fill();
    });
  }, [a, f]);
  return <canvas ref={ref} class="spec-chart small" aria-label="The source's spectrum shape against the ordinary star of the field" />;
}

export function Passport({ a, f }: { a: Anomaly; f: AnomalyField }) {
  const sb = a.identified?.simbad;
  const ga = a.identified?.gaia;
  return (
    <div class="passport">
      <div class="spec-head">
        <b>Anomaly {a.n}</b>
        <span class="mono-s">
          score {a.score.toFixed(2)} · threshold {f.threshold?.toFixed(2)} (field median {f.median?.toFixed(2)})
        </span>
      </div>
      <ul class="drivers">
        {driverWords(a).map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
      <PassportChart a={a} f={f} />
      <div class="mono-s">
        <span style={{ color: '#ffb454' }}>●</span> this source (shape, f / Σf) · <span style={{ color: '#d6e7ed' }}>—</span> the ordinary
        star of the field, fitted to it
      </div>
      <div class="okv">
        <div class="orow">
          <span class="faint">Moves</span>
          <span>
            {a.moves
              ? `yes: ${(Math.hypot(a.pmra, a.pmdec) / 1000).toFixed(2)} ″/yr (search between passes)`
              : 'no (stationary over the passes)'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">SIMBAD</span>
          <span>
            {sb?.state === 'ok'
              ? `${sb.name}${sb.type ? ` · ${sb.type}` : ''}${sb.sp ? ` · ${sb.sp}` : ''}${sb.sep != null ? ` (${sb.sep}″)` : ''}`
              : sb?.state === 'none'
                ? ga?.state === 'ok'
                  ? 'no entry'
                  : 'nothing within 6″'
                : 'not reachable'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">Gaia DR3</span>
          <span>
            {ga?.state === 'ok'
              ? gaiaLine(ga)
              : ga?.state === 'none'
                ? 'nothing within 6″'
                : 'not reachable'}
          </span>
        </div>
        <div class="orow">
          <span class="faint">Points</span>
          <span>
            {a.points} measurements{a.snr != null ? ` · S/N ${a.snr} in the white stack` : ''}
          </span>
        </div>
      </div>
    </div>
  );
}
