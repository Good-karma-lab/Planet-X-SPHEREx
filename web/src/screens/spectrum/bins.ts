import type { SpecPoint } from './types';

export interface Bin {
  lo: number;
  hi: number;
  /** the weighted mean wavelength of the points in the bin, µm */
  lam: number;
  /** the weighted mean flux, mJy */
  flux: number;
  /** one standard error, mJy: the larger of the formal one and the scatter of the points over √n */
  err: number;
  n: number;
  /** the detector most of the points come from (colours the bin) */
  det: number;
  /** at least 3σ above zero; otherwise the bin is drawn as a 3σ upper limit */
  detected: boolean;
}

export const BIN_COUNT = 16;
export const LAM_LO = 0.75;
export const LAM_HI = 5.0;
const MIN_PER_BIN = 3;

/** The used points binned in wavelength (16 bins, equal in log λ): an inverse-variance mean per bin. */
export function binSpectrum(points: SpecPoint[]): Bin[] {
  const edge = (k: number) => LAM_LO * (LAM_HI / LAM_LO) ** (k / BIN_COUNT);
  const out: Bin[] = [];
  for (let k = 0; k < BIN_COUNT; k++) {
    const lo = edge(k);
    const hi = edge(k + 1);
    const pts = points.filter((p) => p.ok && p.flux != null && p.lam >= lo && (p.lam < hi || (k === BIN_COUNT - 1 && p.lam <= hi)));
    if (pts.length < MIN_PER_BIN) continue;
    let sw = 0;
    let sf = 0;
    let sl = 0;
    const perDet = new Map<number, number>();
    for (const p of pts) {
      const w = 1 / Math.max(p.err ?? 0, 1e-6) ** 2;
      sw += w;
      sf += w * p.flux!;
      sl += w * p.lam;
      perDet.set(p.det, (perDet.get(p.det) ?? 0) + 1);
    }
    const flux = sf / sw;
    const n = pts.length;
    const scatter = Math.sqrt(pts.reduce((a, p) => a + (p.flux! - flux) ** 2, 0) / (n - 1));
    const err = Math.max(1 / Math.sqrt(sw), scatter / Math.sqrt(n));
    const det = [...perDet.entries()].sort((a, b) => b[1] - a[1])[0]![0];
    out.push({ lo, hi, lam: sl / sw, flux, err, n, det, detected: flux >= 3 * err });
  }
  return out;
}

/** The line under the chart that says how the bins were made. */
export function binNote(bins: Bin[]): string {
  if (!bins.length) return '';
  const ns = bins.map((b) => b.n);
  const up = bins.filter((b) => !b.detected).length;
  return `binned in wavelength: ${Math.min(...ns)}–${Math.max(...ns)} exposures per bin · ${bins.length - up} of ${bins.length} bins above 3σ (●)${up ? ' · ↓ 3σ upper limit' : ''}`;
}
