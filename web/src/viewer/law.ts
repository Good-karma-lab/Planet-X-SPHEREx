// The display law of the lab (docs/plan/02-methods/2.3-views.md 2.3.1): one law for all passes of a field.

export const LO = -0.05;
export const HI = 400;
export const SOFT = 0.4;

export function stretch(x: number, lo = LO, hi = HI, soft = SOFT): number {
  const s = Math.asinh((x - lo) / soft) / Math.asinh((hi - lo) / soft);
  return Math.min(1, Math.max(0, s));
}
