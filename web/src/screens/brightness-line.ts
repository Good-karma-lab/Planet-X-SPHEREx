/** The plain line about a star's SPHEREx brightness between pass 1 and pass 2, from GET /api/v1/brightness. */

export interface BrightnessResult {
  passes: {
    index: number;
    mjd: number;
    detectors: Record<string, { flux_mjy: number; err_mjy: number; ratio_to_first: number | null; ratio_err: number | null } | null>;
  }[];
  control: { stars: number; scatter: Record<string, number> };
}

const dmag = (ratio: number) => -2.5 * Math.log10(ratio);

/** The wavelength range of each detector band, µm. */
const BAND: Record<string, string> = {
  D1: '0.75–1.1',
  D2: '1.1–1.6',
  D3: '1.6–2.4',
  D4: '2.4–3.8',
  D5: '3.8–4.4',
  D6: '4.4–5.0',
};

/**
 * What the brightness did between pass 1 and 2, detector by detector. A change counts only when it is larger than twice the scatter that
 * the field's own control stars show (the ring photometry of the same unsaturated stars in the same passes); a field without control
 * stars cannot say, and the line says so instead of calling a change real.
 */
export function brightnessLine(name: string, b: BrightnessResult): string | null {
  const second = b.passes[1];
  if (!second) return null;
  const rows: { d: string; m: number; sig: number }[] = [];
  let known = 0;
  for (const [d, v] of Object.entries(second.detectors)) {
    if (!v?.ratio_to_first || v.ratio_to_first <= 0) continue;
    const own = (1.0857 * (v.ratio_err ?? 0)) / v.ratio_to_first;
    const control = b.control.scatter[d];
    if (control != null) known++;
    rows.push({ d, m: dmag(v.ratio_to_first), sig: Math.max(own, control != null ? 1.0857 * control : 0) });
  }
  if (!rows.length) return null;
  if (!known) {
    return `${name}: measured in ${rows.length} detector${rows.length > 1 ? 's' : ''} between pass 1 and 2; this field has too few control stars to tell a real change from scatter.`;
  }
  const real = rows.filter((r) => r.sig > 0 && Math.abs(r.m) > 2 * r.sig);
  const scatter = Math.max(...rows.map((r) => r.sig));
  if (!real.length) return `${name} did not change beyond the scatter (±${scatter.toFixed(2)} mag) between pass 1 and 2.`;
  const part = (sign: number, verb: string) => {
    const rs = real.filter((r) => Math.sign(r.m) === sign);
    return rs.length ? `${verb} ${rs.map((r) => `${Math.abs(r.m).toFixed(1)} mag at ${BAND[r.d] ?? r.d} µm`).join(', ')}` : null;
  };
  const text = [part(-1, 'brightened'), part(1, 'faded')].filter(Boolean).join('; ');
  return `${name} ${text} between pass 1 and 2 (control scatter up to ±${scatter.toFixed(2)} mag).`;
}
