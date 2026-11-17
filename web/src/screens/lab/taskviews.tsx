// What the lab shows for the question of a task about one object (product spec §5b): the spectrum of a red source with the
// spectra of real reference objects of each class over it, and the spectrum of an anomaly with its anomalous bins marked, the
// single exposures of those bins and the detectors they fell on. Every curve is measured by the sky service from SPHEREx exposures.
import { useEffect, useMemo, useRef } from 'preact/hooks';
import { useSpectrum, type Anomaly, type Source } from '../spectrum';
import { binSpectrum, LAM_HI, LAM_LO, type Bin } from '../spectrum/bins';
import { CatalogueCheck, useCatalogue } from '../spectrum/catalogue';
import type { SpecPoint } from '../spectrum/types';

/** The reference objects of the classes a red source may belong to: real objects, their spectra measured by the same service.
 *  Places are J2000 with the proper motion of SIMBAD (mas/yr). */
export const REFERENCES: { result: string; label: string; colour: string; src: Source }[] = [
  {
    result: 'brown_dwarf',
    label: 'Cold brown dwarf: ε Indi B (T1+T6)',
    colour: '#ff9d5c',
    src: { ra: 331.04414658852124, dec: -56.782844195116105, name: 'ε Indi B', pmra: 3981.977, pmdec: -2466.832 },
  },
  {
    result: 'dusty_star',
    label: 'Dusty star: RW Aurigae (T Tauri)',
    colour: '#7fd8ff',
    src: { ra: 76.95652603458333, dec: 30.401438173888888, name: 'RW Aurigae', pmra: 7.23, pmdec: -20.33 },
  },
  {
    result: 'galaxy',
    label: 'Galaxy: NGC 1068 (nucleus)',
    colour: '#c9a0ff',
    src: { ra: 40.669629, dec: -0.013281, name: 'NGC 1068' },
  },
];

const DET_HUE = [270, 220, 175, 95, 40, 5];
const detColour = (d: number) => `hsl(${DET_HUE[d - 1] ?? 0},80%,62%)`;

interface PlotCurve {
  bins: Bin[];
  colour: string;
}

interface PlotProps {
  /** the object's own bins (points with error bars) */
  bins: Bin[];
  /** reference curves, each scaled to the object over the bins both have */
  curves?: PlotCurve[];
  /** wavelength ranges to mark (the anomalous bins), µm */
  ranges?: { lo: number; hi: number }[];
  /** single exposures to draw (the points inside the marked ranges) */
  points?: SpecPoint[];
  testid?: string;
}

/** The scale that brings a reference onto the object: the median ratio over the bins both detect (null when they share fewer than 3). */
export function scaleTo(obj: Bin[], ref: Bin[]): number | null {
  const ratios: number[] = [];
  for (const b of obj) {
    const r = ref.find((q) => Math.abs(Math.log(q.lo / b.lo)) < 1e-6);
    if (r && b.detected && r.detected && r.flux > 0) ratios.push(b.flux / r.flux);
  }
  if (ratios.length < 3) return null;
  ratios.sort((a, b) => a - b);
  return ratios[Math.floor(ratios.length / 2)]!;
}

/** The single exposures inside the marked ranges, and how many of them each detector gave. */
export function pointsIn(points: SpecPoint[], ranges: { lo: number; hi: number }[]): { inside: SpecPoint[]; perDet: number[] } {
  const inside = points.filter((p) => p.ok && p.flux != null && ranges.some((r) => p.lam >= r.lo && p.lam <= r.hi));
  const perDet = [0, 0, 0, 0, 0, 0];
  for (const p of inside) if (p.det >= 1 && p.det <= 6) perDet[p.det - 1]!++;
  return { inside, perDet };
}

const PLOT_H = 132;

/** A small spectrum: flux against wavelength (0.75–5 µm, log), the object's bins with their errors, and what is laid over them. */
function SpecPlot({ bins, curves = [], ranges = [], points = [], testid }: PlotProps) {
  const cv = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const W = c.clientWidth || 320;
    const H = PLOT_H;
    const dpr = typeof devicePixelRatio === 'number' ? devicePixelRatio : 1;
    c.width = Math.round(W * dpr);
    c.height = Math.round(H * dpr);
    const g = c.getContext('2d')!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const L = 30;
    const R = 6;
    const T = 6;
    const B = 16;
    const x = (lam: number) => L + ((W - L - R) * Math.log(lam / LAM_LO)) / Math.log(LAM_HI / LAM_LO);
    const scaled = curves
      .map((cu) => ({ cu, k: scaleTo(bins, cu.bins) }))
      .filter((q): q is { cu: PlotCurve; k: number } => q.k != null);
    const vals = [
      ...bins.map((b) => b.flux + (b.detected ? b.err : 0)),
      ...scaled.flatMap((q) => q.cu.bins.filter((b) => b.detected).map((b) => b.flux * q.k)),
      ...points.map((p) => p.flux ?? 0),
    ].filter((v) => Number.isFinite(v));
    const lows = [...bins.map((b) => b.flux - b.err), ...points.map((p) => p.flux ?? 0)].filter((v) => Number.isFinite(v));
    const hi = vals.length ? Math.max(...vals) : 1;
    const lo = Math.min(0, lows.length ? Math.min(...lows) : 0);
    const span = hi - lo || 1;
    const y = (f: number) => T + (H - T - B) * (1 - (f - lo) / span);
    // the marked ranges (the anomalous bins)
    g.fillStyle = 'rgba(255,90,168,0.16)';
    for (const r of ranges) g.fillRect(x(Math.max(LAM_LO, r.lo)), T, x(Math.min(LAM_HI, r.hi)) - x(Math.max(LAM_LO, r.lo)), H - T - B);
    // axes: wavelength ticks, the zero line
    g.strokeStyle = 'rgba(160,200,215,0.25)';
    g.lineWidth = 1;
    g.strokeRect(L + 0.5, T + 0.5, W - L - R - 1, H - T - B - 1);
    g.font = '500 9.5px ui-monospace, Menlo, monospace';
    g.fillStyle = 'rgba(190,215,225,0.75)';
    g.textAlign = 'center';
    for (const t of [1, 2, 3, 4, 5]) {
      g.beginPath();
      g.moveTo(x(t), H - B);
      g.lineTo(x(t), H - B + 3);
      g.stroke();
      g.fillText(t === 5 ? '5 µm' : String(t), Math.min(x(t), W - R - 12), H - 3);
    }
    g.textAlign = 'right';
    g.fillText(hi >= 100 ? hi.toFixed(0) : hi >= 1 ? hi.toFixed(1) : hi.toFixed(2), L - 3, T + 8);
    g.fillText('0', L - 3, y(0) + 3);
    g.textAlign = 'left';
    if (lo < 0) {
      g.beginPath();
      g.moveTo(L, y(0));
      g.lineTo(W - R, y(0));
      g.stroke();
    }
    // reference curves, each scaled to the object
    for (const { cu, k } of scaled) {
      g.strokeStyle = cu.colour;
      g.lineWidth = 1.4;
      g.beginPath();
      let pen = false;
      for (const b of cu.bins) {
        if (!b.detected) {
          pen = false;
          continue;
        }
        if (pen) g.lineTo(x(b.lam), y(b.flux * k));
        else g.moveTo(x(b.lam), y(b.flux * k));
        pen = true;
      }
      g.stroke();
    }
    // single exposures of the marked ranges, coloured by their detector
    for (const p of points) {
      g.fillStyle = detColour(p.det);
      g.globalAlpha = 0.85;
      g.beginPath();
      g.arc(x(p.lam), y(p.flux ?? 0), 1.7, 0, 2 * Math.PI);
      g.fill();
    }
    g.globalAlpha = 1;
    // the object's bins: a point with its error, an arrow for an upper limit
    for (const b of bins) {
      const px = x(b.lam);
      g.strokeStyle = '#e8f6f4';
      g.fillStyle = '#ffffff';
      g.lineWidth = 1;
      if (b.detected) {
        g.beginPath();
        g.moveTo(px, y(b.flux - b.err));
        g.lineTo(px, y(b.flux + b.err));
        g.stroke();
        g.beginPath();
        g.arc(px, y(b.flux), 2.3, 0, 2 * Math.PI);
        g.fill();
      } else {
        const top = y(Math.max(0, b.flux) + 3 * b.err);
        g.beginPath();
        g.moveTo(px - 3, top);
        g.lineTo(px + 3, top);
        g.moveTo(px, top);
        g.lineTo(px, top + 6);
        g.stroke();
      }
    }
  });
  return <canvas ref={cv} class="tv-plot" style={{ height: `${PLOT_H}px` }} role="img" aria-label="Spectrum" data-testid={testid} />;
}

/** One status line under a plot: how far the object's spectrum is. */
function specLine(view: { state?: string; measured?: number; total?: number } | null, n: number, error: string): string {
  if (error) return `Spectrum: ${error}`;
  if (!view) return 'Spectrum: finding the exposures…';
  if (view.state !== 'done') return `Spectrum: measuring ${view.measured ?? 0} of ${view.total ?? 0} exposures`;
  return `Spectrum: ${n} points`;
}

/** "Why is it red?": the source's spectrum under the spectra of real objects of each class, and its 2MASS and WISE fluxes. */
export function RedView({ src }: { src: Source | null }) {
  const own = useSpectrum(src);
  const r0 = useSpectrum(src ? REFERENCES[0]!.src : null);
  const r1 = useSpectrum(src ? REFERENCES[1]!.src : null);
  const r2 = useSpectrum(src ? REFERENCES[2]!.src : null);
  const refs = [r0, r1, r2];
  const bins = useMemo(() => binSpectrum(own.points), [own.points]);
  const curves = refs.map((r, k) => ({ bins: binSpectrum(r.points), colour: REFERENCES[k]!.colour }));
  const cat = useCatalogue(src, own.view?.state === 'done');
  const usable = own.points.filter((p) => p.ok).length;
  return (
    <div class="tv" data-testid="task-red">
      <SpecPlot bins={bins} curves={curves} testid="task-spectrum" />
      <div class="tv-legend">
        {REFERENCES.map((r, k) => {
          const got = scaleTo(bins, curves[k]!.bins) != null;
          const st = refs[k]!.view?.state;
          return (
            <span key={r.result} class={got ? '' : 'off'}>
              <i style={{ background: r.colour }} />
              {r.label}
              {got ? '' : st === 'done' || refs[k]!.error ? ' · no spectrum' : ' · measuring…'}
            </span>
          );
        })}
      </div>
      <div class="tv-line" data-testid="task-spectrum-line">
        {specLine(own.view, usable, own.error)}
      </div>
      <div class="tv-cat">
        <CatalogueCheck a={cat} ready={own.view?.state === 'done' && usable > 0} />
      </div>
    </div>
  );
}

/** "Is this feature real?": the spectrum with the anomalous bins marked, the single exposures of those bins, and the detectors they fell on. */
export function AnomalyView({ src, anomaly }: { src: Source | null; anomaly: Anomaly | null }) {
  const own = useSpectrum(src);
  const bins = useMemo(() => binSpectrum(own.points), [own.points]);
  const ranges = anomaly?.drivers.map((d) => ({ lo: d.lo, hi: d.hi })) ?? [];
  const { inside, perDet } = useMemo(() => pointsIn(own.points, ranges), [own.points, anomaly?.n]);
  const usable = own.points.filter((p) => p.ok).length;
  const nights = new Set(inside.map((p) => Math.floor(p.mjd))).size;
  return (
    <div class="tv" data-testid="task-anomaly">
      <SpecPlot bins={bins} ranges={ranges} points={inside} testid="task-spectrum" />
      <div class="tv-dets" title="The detectors the exposures of the anomalous bins fell on: a feature of one detector edge is an artefact">
        {perDet.map((n, k) => (
          <span key={k} class={n ? 'on' : ''} style={{ borderColor: n ? detColour(k + 1) : undefined }}>
            D{k + 1}
            <b>{n}</b>
          </span>
        ))}
      </div>
      <div class="tv-line" data-testid="task-spectrum-line">
        {anomaly
          ? `${anomaly.drivers.map((d) => `${d.lo.toFixed(2)}–${d.hi.toFixed(2)} µm ${d.z >= 0 ? '+' : '−'}${Math.abs(d.z).toFixed(0)}σ`).join(' · ')} · ${inside.length} exposures on ${nights} night${nights === 1 ? '' : 's'}`
          : 'No anomaly of the search at this place'}
      </div>
      <div class="tv-line">{specLine(own.view, usable, own.error)}</div>
    </div>
  );
}

/** "Does it really move?": what the Blink and the ruler were set to, and what the search measured. */
export function MoverView({ blink, ruler, search }: { blink: string; ruler: string; search: string }) {
  return (
    <div class="tv" data-testid="task-mover">
      <div class="tv-row">
        <b>Blink</b>
        <span>{blink}</span>
      </div>
      <div class="tv-row">
        <b>Ruler</b>
        <span>{ruler}</span>
      </div>
      <div class="tv-row">
        <b>Search</b>
        <span>{search}</span>
      </div>
    </div>
  );
}
