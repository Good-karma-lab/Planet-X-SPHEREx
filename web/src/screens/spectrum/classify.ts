// What an object probably is, read from its SPHEREx spectrum: a blackbody fitted to the binned points gives a temperature (and the class it belongs to), and the
// bins are checked for the features that say more (methane dip at 3.3 µm, water near 1.4 and 1.9 µm, PAH emission at 3.3 µm, a red continuum that rises).
// One function serves the Spectrum tab's header and the Object tab's passport. It only ever says "the spectrum suggests": it never names the object.

import type { Bin } from './bins';

const C2 = 14387.77; // hc/k, µm K

/** Blackbody shape in F_ν (arbitrary scale) at wavelength λ µm and temperature T. */
const planck = (lam: number, T: number): number => lam ** -3 / Math.expm1(C2 / (lam * T));

export interface Fit {
  /** best temperature, K */
  T: number;
  /** the range inside one sigma (Δχ² ≤ 1 after the errors are scaled up to the scatter), K */
  lo: number;
  hi: number;
  /** reduced χ² of the best fit (before scaling): a high value means bands and edges, not a smooth blackbody */
  chi2: number;
  /** bins used */
  n: number;
}

const GRID = Array.from({ length: 90 }, (_, k) => 500 * (14000 / 500) ** (k / 89));

/** Blackbody fit to the detected bins; null when there are too few. */
export function fitBlackbody(bins: Bin[]): Fit | null {
  const use = bins.filter((b) => b.detected && b.flux > 0);
  if (use.length < 5) return null;
  const w = use.map((b) => 1 / Math.max(b.err, 0.05 * b.flux) ** 2);
  const chi = GRID.map((T) => {
    const m = use.map((b) => planck(b.lam, T));
    let sfm = 0;
    let smm = 0;
    use.forEach((b, i) => {
      sfm += w[i]! * b.flux * m[i]!;
      smm += w[i]! * m[i]! * m[i]!;
    });
    const s = sfm / smm;
    return use.reduce((a, b, i) => a + w[i]! * (b.flux - s * m[i]!) ** 2, 0);
  });
  let k0 = 0;
  chi.forEach((c, k) => {
    if (c < chi[k0]!) k0 = k;
  });
  const nu = use.length - 2;
  const red = chi[k0]! / nu;
  const scale = Math.max(1, red);
  const ok = GRID.filter((_, k) => chi[k]! / scale <= chi[k0]! / scale + 1);
  return { T: GRID[k0]!, lo: Math.min(...ok), hi: Math.max(...ok), chi2: red, n: use.length };
}

/** The class a temperature belongs to, as people say it. */
export function classOf(T: number): string {
  if (T >= 10000) return 'a hot star (A-type or hotter)';
  if (T >= 7500) return 'an A/F-type star';
  if (T >= 6000) return 'an F-type star';
  if (T >= 5200) return 'a G-type star';
  if (T >= 3900) return 'a K-type star';
  if (T >= 3400) return 'an M-type star';
  if (T >= 2300) return 'a cool M dwarf';
  if (T >= 1500) return 'a very cool late-M or L dwarf';
  return 'a very cold brown dwarf';
}

const round = (T: number): number => (T >= 3000 ? Math.round(T / 100) * 100 : Math.round(T / 50) * 50);
const fmtT = (T: number): string => round(T).toLocaleString('en-US');

/** Mean flux of the bins whose wavelength lies in [a, b] µm (null when none is detected). */
function band(bins: Bin[], a: number, b: number): number | null {
  const s = bins.filter((x) => x.detected && x.lam >= a && x.lam <= b);
  return s.length ? s.reduce((t, x) => t + x.flux, 0) / s.length : null;
}

/** How deep a feature at [a, b] µm lies under the continuum drawn between the bins on both sides (ratio of the flux to the continuum; < 1 is a dip). */
function depth(bins: Bin[], a: number, b: number, side: number): number | null {
  const mid = bins.filter((x) => x.detected && x.lam >= a && x.lam <= b);
  const l = bins.filter((x) => x.detected && x.lam < a && x.lam >= a / side);
  const r = bins.filter((x) => x.detected && x.lam > b && x.lam <= b * side);
  if (!mid.length || !l.length || !r.length) return null;
  const fm = mid.reduce((t, x) => t + x.flux, 0) / mid.length;
  const lm = l[l.length - 1]!;
  const rm = r[0]!;
  const lm0 = mid.reduce((t, x) => t + x.lam, 0) / mid.length;
  // the continuum at the feature: a straight line in log-log between the bins on each side
  const t = Math.log(lm0 / lm.lam) / Math.log(rm.lam / lm.lam);
  const cont = Math.exp(Math.log(lm.flux) * (1 - t) + Math.log(rm.flux) * t);
  const err = Math.hypot(mid.reduce((t2, x) => t2 + x.err, 0) / mid.length / fm, 0.08);
  const ratio = fm / cont;
  // a feature counts only when it stands out of the errors
  return Math.abs(ratio - 1) > 2 * err ? ratio : 1;
}

export interface Guess {
  /** the line that leads: the catalogue name when known, then what the spectrum suggests */
  head: string;
  /** the features seen, as a short line ('' when none) */
  features: string;
  /** ok: a class could be read; thin: too few points; saturated: the star overflows the detector; wait: still measuring */
  kind: 'ok' | 'thin' | 'saturated' | 'wait';
  T?: number;
}

export interface GuessInput {
  bins: Bin[];
  usable: number;
  saturated: boolean;
  running: boolean;
  /** a catalogue name (the test target's, or SIMBAD's) and what the catalogue says it is */
  name?: string | null;
  type?: string | null;
  sp?: string | null;
}

/** What the object probably is. */
export function guessObject(a: GuessInput): Guess {
  const lead = a.name ? `${a.name}${a.sp || a.type ? ` (SIMBAD: ${a.sp ?? a.type})` : ''}` : '';
  const say = (s: string) => (lead ? `${lead} · ${s}` : s.charAt(0).toUpperCase() + s.slice(1));
  if (a.saturated) return { head: say('saturated in most exposures: no SPHEREx spectrum to read a class from'), features: '', kind: 'saturated' };
  if (a.running && a.usable < 8) return { head: say('reading the spectrum…'), features: '', kind: 'wait' };
  const fit = fitBlackbody(a.bins);
  const det = a.bins.filter((b) => b.detected).length;
  if (!fit || det < 5) return { head: say(`too few points to say what it is (${a.usable} usable, ${det} bins above 3σ)`), features: '', kind: 'thin' };

  const feats: string[] = [];
  const blue = band(a.bins, 1.0, 1.6);
  const red = band(a.bins, 4.0, 5.0);
  const ch4 = depth(a.bins, 3.1, 3.5, 1.5);
  const pah = ch4 !== null && ch4 > 1 ? ch4 : null;
  const water14 = depth(a.bins, 1.3, 1.55, 1.3);
  const water19 = depth(a.bins, 1.75, 2.05, 1.3);
  const rising = blue !== null && red !== null && red > blue;
  if (ch4 !== null && ch4 < 0.8) feats.push('a methane dip near 3.3 µm');
  if (water14 !== null && water14 < 0.85) feats.push('water near 1.4 µm');
  if (water19 !== null && water19 < 0.85) feats.push('water near 1.9 µm');
  if (pah !== null && pah > 1.25) feats.push('PAH emission at 3.3 µm (dusty, or a galaxy)');
  if (rising && fit.T < 1800) feats.push('a rising red continuum (dust-reddened or very cool)');

  let cls: string;
  let temp: string;
  const hotLimit = fit.hi >= 12000 || fit.hi / fit.lo > 2.2;
  if (ch4 !== null && ch4 < 0.8) {
    // a cold brown dwarf is no blackbody: the methane dip and the red light say it, not the temperature
    cls = 'a very cold brown dwarf: methane dip at 3.3 µm' + (red !== null && blue !== null && red > 0.5 * blue ? ', bright at 4–5 µm' : '');
    temp = '';
  } else if (hotLimit && fit.T >= 5000) {
    cls = 'a hot star, over about ' + fmtT(Math.max(fit.lo, 5000)) + ' K (the 0.75–5 µm colours cannot tell how hot)';
    temp = '';
  } else {
    cls = classOf(fit.T);
    // the fit stops at 500 K: a spectrum that wants less is simply cold
    temp = fit.T <= 520 ? ', colder than about 600 K' : `, about ${fmtT(fit.T)} K`;
  }
  const poor = fit.chi2 > 6 ? ' (not a smooth blackbody: bands in the spectrum)' : '';
  // with a temperature the catalogue's line says what the fit gave; without one (a methane dwarf is no blackbody) the class alone
  const head = lead
    ? temp
      ? `${lead} · spectrum suggests ${cls}${temp}`
      : `${lead} · spectrum suggests ${cls}`
    : `Spectrum suggests ${cls}${temp}`;
  return { head: head + (temp ? poor : ''), features: feats.join(' · '), kind: 'ok', T: fit.T };
}
